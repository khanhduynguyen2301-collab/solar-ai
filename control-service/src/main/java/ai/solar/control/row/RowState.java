package ai.solar.control.row;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.Instant;

/**
 * SIMULATED position of a controlled row.
 *
 * <p>There is no hardware and no telemetry. When a decision is applied, the row is recorded as having
 * reached the decided angle, so the next decision starts from there.
 */
@Entity
@Table(name = "row_state")
public class RowState {

	@Id
	@Column(name = "row_id", nullable = false, updatable = false)
	private String rowId;

	@Column(name = "angle_deg", nullable = false)
	private double angleDeg;

	@Column(name = "updated_at", nullable = false)
	private Instant updatedAt;

	// Two decisions applied at once must not both win; the second one fails and is reported.
	@Version
	@Column(name = "version", nullable = false)
	private long version;

	protected RowState() {
	}

	public RowState(String rowId, double angleDeg, Instant updatedAt) {
		this.rowId = rowId;
		this.angleDeg = angleDeg;
		this.updatedAt = updatedAt;
	}

	public String getRowId() {
		return rowId;
	}

	public double getAngleDeg() {
		return angleDeg;
	}

	public Instant getUpdatedAt() {
		return updatedAt;
	}

	/** Records the simulated row as now sitting at {@code angleDeg}. */
	public void moveTo(double angleDeg, Instant at) {
		this.angleDeg = angleDeg;
		this.updatedAt = at;
	}
}
