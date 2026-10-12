package ai.solar.control.decision;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.UUID;

/**
 * One decision as the inference API returned it. Rows are written once and never changed.
 *
 * <p>The summary columns are copied out of {@code payload} so history can be filtered and counted;
 * {@code payload} remains the full record. Figures the API did not supply are null, never zero.
 */
@Entity
@Table(name = "decision_record")
public class DecisionRecord {

	/** What caused the request. */
	public enum TriggerSource {
		MANUAL, SCHEDULED
	}

	@Id
	@Column(name = "id", nullable = false, updatable = false)
	private UUID id;

	@Column(name = "created_at", nullable = false, updatable = false)
	private Instant createdAt;

	@Enumerated(EnumType.STRING)
	@Column(name = "trigger_source", nullable = false, updatable = false)
	private TriggerSource triggerSource;

	@Column(name = "mode", nullable = false, updatable = false)
	private String mode;

	@Column(name = "interval_start", nullable = false, updatable = false)
	private Instant intervalStart;

	@Column(name = "control_target_id", nullable = false, updatable = false)
	private String controlTargetId;

	@Column(name = "current_angle_deg", nullable = false, updatable = false)
	private double currentAngleDeg;

	@Column(name = "action", nullable = false, updatable = false)
	private String action;

	@Column(name = "target_angle_deg", nullable = false, updatable = false)
	private double targetAngleDeg;

	@Column(name = "selected_model", updatable = false)
	private String selectedModel;

	@Column(name = "data_status", nullable = false, updatable = false)
	private String dataStatus;

	@Column(name = "safety_passed", nullable = false, updatable = false)
	private boolean safetyPassed;

	@Column(name = "energy_gain_kwh", updatable = false)
	private Double energyGainKwh;

	@Column(name = "movement_cost_kwh_equivalent", updatable = false)
	private Double movementCostKwhEquivalent;

	@Column(name = "net_benefit_kwh_equivalent", updatable = false)
	private Double netBenefitKwhEquivalent;

	@Column(name = "reason", nullable = false, updatable = false, length = 2000)
	private String reason;

	@Column(name = "applied_to_row", nullable = false, updatable = false)
	private boolean appliedToRow;

	@Column(name = "payload", nullable = false, updatable = false, columnDefinition = "TEXT")
	private String payload;

	protected DecisionRecord() {
	}

	DecisionRecord(UUID id, Instant createdAt, TriggerSource triggerSource, String mode, double currentAngleDeg, boolean appliedToRow,
			DecisionFacts facts, String payload) {
		this.id = id;
		this.createdAt = createdAt;
		this.triggerSource = triggerSource;
		this.mode = mode;
		this.currentAngleDeg = currentAngleDeg;
		this.appliedToRow = appliedToRow;
		this.intervalStart = facts.intervalStart();
		this.controlTargetId = facts.controlTargetId();
		this.action = facts.action();
		this.targetAngleDeg = facts.targetAngleDeg();
		this.selectedModel = facts.selectedModel();
		this.dataStatus = facts.dataStatus();
		this.safetyPassed = facts.safetyPassed();
		this.energyGainKwh = facts.energyGainKwh();
		this.movementCostKwhEquivalent = facts.movementCostKwhEquivalent();
		this.netBenefitKwhEquivalent = facts.netBenefitKwhEquivalent();
		this.reason = facts.reason();
		this.payload = payload;
	}

	public UUID getId() {
		return id;
	}

	public Instant getCreatedAt() {
		return createdAt;
	}

	public TriggerSource getTriggerSource() {
		return triggerSource;
	}

	public String getMode() {
		return mode;
	}

	public Instant getIntervalStart() {
		return intervalStart;
	}

	public String getControlTargetId() {
		return controlTargetId;
	}

	public double getCurrentAngleDeg() {
		return currentAngleDeg;
	}

	public String getAction() {
		return action;
	}

	public double getTargetAngleDeg() {
		return targetAngleDeg;
	}

	public String getSelectedModel() {
		return selectedModel;
	}

	public String getDataStatus() {
		return dataStatus;
	}

	public boolean isSafetyPassed() {
		return safetyPassed;
	}

	public Double getEnergyGainKwh() {
		return energyGainKwh;
	}

	public Double getMovementCostKwhEquivalent() {
		return movementCostKwhEquivalent;
	}

	public Double getNetBenefitKwhEquivalent() {
		return netBenefitKwhEquivalent;
	}

	public String getReason() {
		return reason;
	}

	public boolean isAppliedToRow() {
		return appliedToRow;
	}

	public String getPayload() {
		return payload;
	}
}
