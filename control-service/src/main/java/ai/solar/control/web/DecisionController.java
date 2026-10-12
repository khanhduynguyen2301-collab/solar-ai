package ai.solar.control.web;

import ai.solar.control.decision.DecisionRecord;
import ai.solar.control.decision.DecisionRecord.TriggerSource;
import ai.solar.control.decision.DecisionService;
import ai.solar.control.decision.DecisionService.DecisionCommand;
import ai.solar.control.decision.DecisionService.DecisionStats;
import ai.solar.control.row.RowState;
import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.net.URI;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.data.domain.Page;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** Decision requests, history and the simulated row position. */
@RestController
@RequestMapping("/api")
@Validated
public class DecisionController {

	private static final int MAX_PAGE_SIZE = 100;

	private final DecisionService service;

	public DecisionController(DecisionService service) {
		this.service = service;
	}

	/**
	 * @param mode "recorded" (default) or "live"
	 * @param timestamp a recorded hour to replay; omit for the inference API's default hour
	 * @param currentAngleDeg omit to decide from, and then update, the simulated row position; give a value
	 *        for a what-if question that leaves the row where it is
	 * @param explain add LLM explanations (slower)
	 */
	public record DecisionRequest(@Pattern(regexp = "recorded|live", message = "must be recorded or live") String mode,
			@Size(max = 40) String timestamp, @DecimalMin("0") @DecimalMax("90") Double currentAngleDeg, Boolean explain) {

		DecisionCommand toCommand() {
			return new DecisionCommand(mode == null ? "recorded" : mode, timestamp, currentAngleDeg, Boolean.TRUE.equals(explain));
		}
	}

	/** A stored decision without its full payload. Null figures were not supplied by the inference API. */
	public record DecisionView(UUID id, Instant createdAt, TriggerSource trigger, String mode, Instant intervalStart, String controlTargetId,
			double currentAngleDeg, String action, double targetAngleDeg, String selectedModel, String dataStatus, boolean safetyPassed,
			Double energyGainKwh, Double movementCostKwhEquivalent, Double netBenefitKwhEquivalent, String reason, boolean appliedToRow) {

		static DecisionView of(DecisionRecord record) {
			return new DecisionView(record.getId(), record.getCreatedAt(), record.getTriggerSource(), record.getMode(), record.getIntervalStart(),
					record.getControlTargetId(), record.getCurrentAngleDeg(), record.getAction(), record.getTargetAngleDeg(),
					record.getSelectedModel(), record.getDataStatus(), record.isSafetyPassed(), record.getEnergyGainKwh(),
					record.getMovementCostKwhEquivalent(), record.getNetBenefitKwhEquivalent(), record.getReason(), record.isAppliedToRow());
		}
	}

	public record DecisionPage(List<DecisionView> items, int page, int size, long totalItems, int totalPages) {
	}

	/** The SIMULATED row position; no hardware reports it. */
	public record RowView(String rowId, double angleDeg, Instant updatedAt, boolean simulated) {
	}

	@PostMapping("/decisions")
	public ResponseEntity<DecisionView> decide(@Valid @RequestBody(required = false) DecisionRequest request) {
		DecisionRequest effective = request == null ? new DecisionRequest(null, null, null, null) : request;
		DecisionRecord record = service.decide(effective.toCommand(), TriggerSource.MANUAL);
		return ResponseEntity.created(URI.create("/api/decisions/" + record.getId())).body(DecisionView.of(record));
	}

	@GetMapping("/decisions")
	public DecisionPage history(
			@RequestParam(required = false) @Pattern(regexp = "ROTATE|HOLD|STOW", message = "must be ROTATE, HOLD or STOW") String action,
			@RequestParam(required = false) Instant from, @RequestParam(required = false) Instant to,
			@RequestParam(defaultValue = "0") @Min(0) int page, @RequestParam(defaultValue = "20") @Min(1) @Max(MAX_PAGE_SIZE) int size) {
		Page<DecisionRecord> result = service.history(action, from, to, page, size);
		return new DecisionPage(result.getContent().stream().map(DecisionView::of).toList(), result.getNumber(), result.getSize(),
				result.getTotalElements(), result.getTotalPages());
	}

	@GetMapping("/decisions/latest")
	public DecisionView latest() {
		return DecisionView.of(service.latest().orElseThrow(() -> new NotFoundException("No decision has been stored yet")));
	}

	/** The newest decision's full payload: the same JSON the dashboard reads from a file. */
	@GetMapping(value = "/decisions/latest/payload", produces = MediaType.APPLICATION_JSON_VALUE)
	public String latestPayload() {
		return service.latest().orElseThrow(() -> new NotFoundException("No decision has been stored yet")).getPayload();
	}

	@GetMapping("/decisions/{id}")
	public DecisionView one(@PathVariable UUID id) {
		return DecisionView.of(service.find(id).orElseThrow(() -> new NotFoundException("No decision " + id)));
	}

	@GetMapping(value = "/decisions/{id}/payload", produces = MediaType.APPLICATION_JSON_VALUE)
	public String payload(@PathVariable UUID id) {
		return service.find(id).orElseThrow(() -> new NotFoundException("No decision " + id)).getPayload();
	}

	@GetMapping("/stats")
	public DecisionStats stats() {
		return service.stats();
	}

	@GetMapping("/row")
	public RowView row() {
		RowState row = service.currentRow();
		return new RowView(row.getRowId(), row.getAngleDeg(), row.getUpdatedAt(), true);
	}
}
