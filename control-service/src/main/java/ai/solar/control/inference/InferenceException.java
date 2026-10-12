package ai.solar.control.inference;

/**
 * The inference API did not produce a decision.
 *
 * <p>{@code reason} tells the caller whose problem it was, so a bad request is not reported as an outage.
 */
public class InferenceException extends RuntimeException {

	/** Why no decision came back. */
	public enum Reason {
		/** The inference API rejected the request as invalid (for example an unknown hour). */
		REJECTED,
		/** The inference API is rate limiting this service. */
		RATE_LIMITED,
		/** The inference API is unreachable, not ready, timed out, or failed. */
		UNAVAILABLE,
		/** A reply arrived but is not a usable decision payload. */
		MALFORMED
	}

	private final Reason reason;

	public InferenceException(Reason reason, String message, Throwable cause) {
		super(message, cause);
		this.reason = reason;
	}

	public InferenceException(Reason reason, String message) {
		this(reason, message, null);
	}

	public Reason reason() {
		return reason;
	}
}
