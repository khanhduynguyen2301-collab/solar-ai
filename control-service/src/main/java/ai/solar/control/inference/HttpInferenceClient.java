package ai.solar.control.inference;

import ai.solar.control.config.SolarProperties;
import ai.solar.control.inference.InferenceException.Reason;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.http.client.ClientHttpRequestFactoryBuilder;
import org.springframework.boot.http.client.HttpClientSettings;
import org.springframework.http.HttpStatusCode;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/** Calls {@code POST /recommendations} on the Python inference API. One attempt per call, no retries. */
@Component
public class HttpInferenceClient implements InferenceClient {

	private static final String API_KEY_HEADER = "X-API-Key";
	private static final int UNAUTHORIZED = 401;
	private static final int FORBIDDEN = 403;
	private static final int TOO_MANY_REQUESTS = 429;

	private final RestClient restClient;
	private final ObjectMapper objectMapper;

	public HttpInferenceClient(RestClient.Builder builder, SolarProperties properties, ObjectMapper objectMapper) {
		SolarProperties.Inference inference = properties.inference();
		HttpClientSettings settings = HttpClientSettings.defaults()
			.withConnectTimeout(inference.connectTimeout())
			.withReadTimeout(inference.readTimeout());
		RestClient.Builder configured = builder.baseUrl(inference.baseUrl().toString())
			.requestFactory(ClientHttpRequestFactoryBuilder.detect().build(settings));
		if (inference.hasApiKey()) {
			configured = configured.defaultHeader(API_KEY_HEADER, inference.apiKey());
		}
		this.restClient = configured.build();
		this.objectMapper = objectMapper;
	}

	@Override
	public JsonNode recommend(InferenceRequest request) {
		Map<String, Object> body = new LinkedHashMap<>();
		body.put("mode", request.mode());
		if (request.timestamp() != null) {
			body.put("timestamp", request.timestamp());
		}
		body.put("current_angle_deg", request.currentAngleDeg());
		body.put("explain", request.explain());
		String reply;
		try {
			reply = restClient.post()
				.uri("/recommendations")
				.contentType(MediaType.APPLICATION_JSON)
				.accept(MediaType.APPLICATION_JSON)
				.body(body)
				.retrieve()
				.body(String.class);
		}
		catch (RestClientResponseException ex) {
			throw new InferenceException(reasonFor(ex.getStatusCode()), describe(ex), ex);
		}
		catch (ResourceAccessException ex) {
			throw new InferenceException(Reason.UNAVAILABLE, "The inference API could not be reached", ex);
		}
		return parse(reply);
	}

	private JsonNode parse(String reply) {
		if (reply == null || reply.isBlank()) {
			throw new InferenceException(Reason.MALFORMED, "The inference API returned an empty reply");
		}
		try {
			return objectMapper.readTree(reply);
		}
		catch (JacksonException ex) {
			throw new InferenceException(Reason.MALFORMED, "The inference API returned a reply that is not JSON", ex);
		}
	}

	private static Reason reasonFor(HttpStatusCode status) {
		if (status.value() == TOO_MANY_REQUESTS) {
			return Reason.RATE_LIMITED;
		}
		// A refused key is this service's configuration, not something the caller sent.
		if (isRefusedKey(status)) {
			return Reason.UNAVAILABLE;
		}
		// 404 (unknown hour) and 422 (invalid field) are the caller's mistake; everything else is the API's state.
		return status.is4xxClientError() ? Reason.REJECTED : Reason.UNAVAILABLE;
	}

	private static boolean isRefusedKey(HttpStatusCode status) {
		return status.value() == UNAUTHORIZED || status.value() == FORBIDDEN;
	}

	/** The inference API's own error message when it sent one in its envelope, otherwise just the status. */
	private String describe(RestClientResponseException ex) {
		if (isRefusedKey(ex.getStatusCode())) {
			return "The inference API refused this service's API key (HTTP " + ex.getStatusCode().value() + "); check SOLAR_INFERENCE_API_KEY";
		}
		try {
			JsonNode message = objectMapper.readTree(ex.getResponseBodyAsString()).path("error").path("message");
			if (message.isString() && !message.asString().isBlank()) {
				return message.asString();
			}
		}
		catch (JacksonException ignored) {
			// Not the envelope; fall through to the status-only message.
		}
		return "The inference API answered with HTTP " + ex.getStatusCode().value();
	}
}
