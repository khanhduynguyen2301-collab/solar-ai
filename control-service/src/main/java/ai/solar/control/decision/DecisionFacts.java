package ai.solar.control.decision;

import ai.solar.control.inference.InferenceException;
import ai.solar.control.inference.InferenceException.Reason;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;
import tools.jackson.databind.JsonNode;

/**
 * The fields this service reads out of an inference payload. Nothing is derived or recalculated:
 * each value is copied as the inference API wrote it, and optimization figures stay null when the
 * payload has no optimization result.
 */
record DecisionFacts(Instant intervalStart, String controlTargetId, String action, double targetAngleDeg, String selectedModel,
		String dataStatus, boolean safetyPassed, Double energyGainKwh, Double movementCostKwhEquivalent, Double netBenefitKwhEquivalent,
		String reason) {

	private static final Set<String> ACTIONS = Set.of("ROTATE", "HOLD", "STOW");
	private static final int MAX_REASON_LENGTH = 2000;

	/**
	 * @throws InferenceException with {@link Reason#MALFORMED} when a required field is missing or has the wrong type
	 */
	static DecisionFacts from(JsonNode payload) {
		JsonNode decision = payload.path("decision");
		JsonNode optimization = payload.path("optimization");
		String action = requiredText(decision, "action", "decision.action");
		if (!ACTIONS.contains(action)) {
			throw malformed("decision.action is not ROTATE, HOLD or STOW");
		}
		String reason = requiredText(decision, "reason", "decision.reason");
		return new DecisionFacts(
				instant(requiredText(payload.path("metadata"), "interval_start", "metadata.interval_start")),
				requiredText(payload.path("metadata"), "control_target_id", "metadata.control_target_id"),
				action,
				requiredNumber(decision, "target_angle_deg", "decision.target_angle_deg"),
				payload.path("selected_model").isString() ? payload.path("selected_model").asString() : null,
				requiredText(payload.path("data_agent"), "status", "data_agent.status"),
				requiredBoolean(payload.path("safety"), "passed", "safety.passed"),
				optionalNumber(optimization, "energy_gain_kwh"),
				optionalNumber(optimization, "movement_cost_kwh_equivalent"),
				optionalNumber(optimization, "net_benefit_kwh_equivalent"),
				reason.length() > MAX_REASON_LENGTH ? reason.substring(0, MAX_REASON_LENGTH) : reason);
	}

	private static String requiredText(JsonNode parent, String field, String path) {
		JsonNode node = parent.path(field);
		if (!node.isString() || node.asString().isBlank()) {
			throw malformed(path + " is missing");
		}
		return node.asString();
	}

	private static double requiredNumber(JsonNode parent, String field, String path) {
		JsonNode node = parent.path(field);
		if (!node.isNumber()) {
			throw malformed(path + " is missing");
		}
		return node.asDouble();
	}

	private static boolean requiredBoolean(JsonNode parent, String field, String path) {
		JsonNode node = parent.path(field);
		if (!node.isBoolean()) {
			throw malformed(path + " is missing");
		}
		return node.asBoolean();
	}

	private static Double optionalNumber(JsonNode parent, String field) {
		JsonNode node = parent.path(field);
		return node.isNumber() ? node.asDouble() : null;
	}

	private static Instant instant(String value) {
		try {
			return Instant.parse(value);
		}
		catch (DateTimeParseException ex) {
			throw malformed("metadata.interval_start is not a timestamp");
		}
	}

	private static InferenceException malformed(String detail) {
		return new InferenceException(Reason.MALFORMED, "The inference API returned an unusable decision: " + detail);
	}
}
