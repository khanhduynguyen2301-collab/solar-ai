package ai.solar.control.decision;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;

public interface DecisionRepository extends JpaRepository<DecisionRecord, UUID>, JpaSpecificationExecutor<DecisionRecord> {

	Optional<DecisionRecord> findFirstByOrderByCreatedAtDesc();

	/** How many stored decisions chose each action. */
	@Query("select d.action as action, count(d) as total from DecisionRecord d group by d.action")
	List<ActionCount> countByAction();

	/**
	 * Sum of the net benefit the inference API reported for applied ROTATE decisions; null when there are none.
	 * HOLD and STOW are excluded because no move was made, so their reported benefit was not realized.
	 */
	@Query("select sum(d.netBenefitKwhEquivalent) from DecisionRecord d where d.appliedToRow = true and d.action = 'ROTATE'")
	Double sumNetBenefitOfAppliedRotations();

	@Query("select min(d.createdAt) from DecisionRecord d")
	Instant earliestCreatedAt();

	@Query("select max(d.createdAt) from DecisionRecord d")
	Instant latestCreatedAt();

	/** One row of {@link #countByAction()}. */
	interface ActionCount {

		String getAction();

		long getTotal();
	}
}
