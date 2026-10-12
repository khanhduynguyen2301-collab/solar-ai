package ai.solar.control.inference;

import tools.jackson.databind.JsonNode;

/** The Python inference API, as far as this service needs it. */
public interface InferenceClient {

	/**
	 * Asks for one decision.
	 *
	 * @return the validated dashboard payload exactly as the inference API returned it
	 * @throws InferenceException when no decision could be obtained
	 */
	JsonNode recommend(InferenceRequest request);

	/**
	 * What to ask for. A null field is left out of the request, so the inference API applies its own default.
	 *
	 * @param mode "recorded" or "live"
	 * @param timestamp the recorded hour to replay; null for the API's default hour, unused in live mode
	 * @param currentAngleDeg the row's current tilt
	 * @param explain whether to add LLM explanations
	 */
	record InferenceRequest(String mode, String timestamp, double currentAngleDeg, boolean explain) {
	}
}
