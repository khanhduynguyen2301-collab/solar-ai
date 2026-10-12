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

	private static final int TOO_MANY_REQUESTS = 429;

	private final RestClient restClient;
	private final ObjectMapper objectMapper;

	public HttpInferenceClient(RestClient.Builder builder, SolarProperties properties, ObjectMapper objectMapper) {
		SolarProperties.Inference inference = properties.inference();
		HttpClientSettings settings = HttpClientSettings.defaults()
			.withConnectTimeout(inference.connectTimeout())
			.withReadTimeout(inference.readTimeout());
		this.restClient = builder.baseUrl(inference.baseUrl().toString())
			.requestFactory(ClientHttpRequestFactoryBuilder.detect().build(settings))
			.build();
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
		// 404 (unknown hour) and 422 (invalid field) are the caller's mistake; everything else is the API's state.
		return status.is4xxClientError() ? Reason.REJECTED : Reason.UNAVAILABLE;
	}

	/** The inference API's own error message when it sent one in its envelope, otherwise just the status. */
	private String describe(RestClientResponseException ex) {
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
