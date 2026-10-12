package ai.solar.control.decision;

import ai.solar.control.config.SolarProperties;
import ai.solar.control.decision.DecisionRecord.TriggerSource;
import ai.solar.control.inference.InferenceClient;
import ai.solar.control.inference.InferenceClient.InferenceRequest;
import ai.solar.control.row.RowState;
import ai.solar.control.row.RowStateRepository;
import java.time.Clock;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.locks.ReentrantLock;
import java.util.stream.Collectors;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;

/**
 * Asks the inference API for decisions, stores them, and keeps the simulated row position.
 *
 * <p>This class decides nothing. It forwards the request, copies the reply, and, for an applied
 * decision, records the row as having moved to the angle the inference API chose.
 */
@Service
public class DecisionService {

	/** Actions after which the simulated row sits at the decided angle. A HOLD leaves it where it was. */
	private static final Set<String> MOVING_ACTIONS = Set.of("ROTATE", "STOW");

	private final InferenceClient inferenceClient;
	private final DecisionRepository decisions;
	private final RowStateRepository rows;
	private final SolarProperties properties;
	private final TransactionTemplate transactions;
	private final Clock clock;
	// Applied decisions run one at a time, so each starts from the position the previous one left.
	// This holds for a single instance of the service; several instances would need a database lock.
	private final ReentrantLock appliedDecisions = new ReentrantLock();

	public DecisionService(InferenceClient inferenceClient, DecisionRepository decisions, RowStateRepository rows, SolarProperties properties,
			TransactionTemplate transactions, Clock clock) {
		this.inferenceClient = inferenceClient;
		this.decisions = decisions;
		this.rows = rows;
		this.properties = properties;
		this.transactions = transactions;
		this.clock = clock;
	}

	/**
	 * What to ask for.
	 *
	 * @param currentAngleDeg null to use, and afterwards update, the simulated row position; a value
	 *        asks a what-if question from that angle and leaves the row position alone
	 */
	public record DecisionCommand(String mode, String timestamp, Double currentAngleDeg, boolean explain) {
	}

	public DecisionRecord decide(DecisionCommand command, TriggerSource trigger) {
		if (command.currentAngleDeg() != null) {
			return requestAndStore(command, command.currentAngleDeg(), false, trigger);
		}
		appliedDecisions.lock();
		try {
			return requestAndStore(command, currentRow().getAngleDeg(), true, trigger);
		}
		finally {
			appliedDecisions.unlock();
		}
	}

	private DecisionRecord requestAndStore(DecisionCommand command, double angleDeg, boolean applied, TriggerSource trigger) {
		// The network call is made outside any database transaction, so a slow reply holds no connection.
		JsonNode payload = inferenceClient.recommend(new InferenceRequest(command.mode(), command.timestamp(), angleDeg, command.explain()));
		DecisionFacts facts = DecisionFacts.from(payload);
		return transactions.execute(status -> {
			Instant now = clock.instant();
			DecisionRecord saved = decisions.save(new DecisionRecord(UUID.randomUUID(), now, trigger, command.mode(), angleDeg, applied, facts, payload.toString()));
			if (applied && MOVING_ACTIONS.contains(facts.action())) {
				RowState row = currentRow();
				row.moveTo(facts.targetAngleDeg(), now);
				rows.save(row);
			}
			return saved;
		});
	}

	/** The simulated row, created at the configured starting angle the first time it is needed. */
	public RowState currentRow() {
		String rowId = properties.row().id();
		return rows.findById(rowId).orElseGet(() -> rows.save(new RowState(rowId, properties.row().initialAngleDeg(), clock.instant())));
	}

	public Optional<DecisionRecord> find(UUID id) {
		return decisions.findById(id);
	}

	public Optional<DecisionRecord> latest() {
		return decisions.findFirstByOrderByCreatedAtDesc();
	}

	/** Newest first. Null filters are not applied. */
	public Page<DecisionRecord> history(String action, Instant from, Instant to, int page, int size) {
		List<Specification<DecisionRecord>> filters = new ArrayList<>();
		if (action != null) {
			filters.add((root, query, builder) -> builder.equal(root.get("action"), action));
		}
		if (from != null) {
			filters.add((root, query, builder) -> builder.greaterThanOrEqualTo(root.get("createdAt"), from));
		}
		if (to != null) {
			filters.add((root, query, builder) -> builder.lessThan(root.get("createdAt"), to));
		}
		return decisions.findAll(Specification.allOf(filters), PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt")));
	}

	public DecisionStats stats() {
		Map<String, Long> byAction = decisions.countByAction().stream()
			.collect(Collectors.toMap(DecisionRepository.ActionCount::getAction, DecisionRepository.ActionCount::getTotal));
		return new DecisionStats(byAction.values().stream().mapToLong(Long::longValue).sum(), byAction,
				decisions.sumNetBenefitOfAppliedRotations(), decisions.earliestCreatedAt(), decisions.latestCreatedAt());
	}

	/**
	 * Counts and one sum over the stored decisions.
	 *
	 * @param netBenefitOfAppliedRotationsKwhEquivalent the inference API's reported net benefit, summed over applied
	 *        ROTATE decisions; null when there are none. These are model predictions for a simulated row, not measured energy.
	 */
	public record DecisionStats(long total, Map<String, Long> byAction, Double netBenefitOfAppliedRotationsKwhEquivalent, Instant first,
			Instant last) {
	}
}
