package ai.solar.control.inference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import ai.solar.control.Fixtures;
import ai.solar.control.config.SolarProperties;
import ai.solar.control.inference.InferenceClient.InferenceRequest;
import ai.solar.control.inference.InferenceException.Reason;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** The client against a real local HTTP server, so request shape, status mapping and timeouts are all exercised. */
class HttpInferenceClientTests {

	private HttpServer server;
	private final AtomicReference<String> receivedBody = new AtomicReference<>();
	private final AtomicReference<String> receivedPath = new AtomicReference<>();
	private volatile int status = 200;
	private volatile String reply = Fixtures.rotatePayloadText();
	private volatile long delayMillis = 0;

	@BeforeEach
	void startServer() throws IOException {
		server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
		server.createContext("/", exchange -> {
			receivedPath.set(exchange.getRequestMethod() + " " + exchange.getRequestURI().getPath());
			receivedBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
			try {
				Thread.sleep(delayMillis);
			}
			catch (InterruptedException ex) {
				Thread.currentThread().interrupt();
			}
			byte[] bytes = reply.getBytes(StandardCharsets.UTF_8);
			exchange.getResponseHeaders().add("content-type", "application/json");
			exchange.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
			if (bytes.length > 0) {
				exchange.getResponseBody().write(bytes);
			}
			exchange.close();
		});
		server.start();
	}

	@AfterEach
	void stopServer() {
		server.stop(0);
	}

	private HttpInferenceClient client(Duration readTimeout) {
		URI baseUrl = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
		SolarProperties properties = new SolarProperties(new SolarProperties.Inference(baseUrl, Duration.ofSeconds(2), readTimeout),
				new SolarProperties.Row("row-001", 35), new SolarProperties.Schedule(false, "0 5 * * * *", "live"));
		return new HttpInferenceClient(RestClient.builder(), properties, JsonMapper.builder().build());
	}

	private HttpInferenceClient client() {
		return client(Duration.ofSeconds(5));
	}

	@Test
	void postsTheRequestInTheInferenceApisFieldNamesAndReturnsItsPayload() {
		JsonNode payload = client().recommend(new InferenceRequest("recorded", "2025-06-05T19:00:00Z", 60, true));

		JsonNode sent = Fixtures.parse(receivedBody.get());
		assertThat(receivedPath.get()).isEqualTo("POST /recommendations");
		assertThat(sent.get("mode").asString()).isEqualTo("recorded");
		assertThat(sent.get("timestamp").asString()).isEqualTo("2025-06-05T19:00:00Z");
		assertThat(sent.get("current_angle_deg").asDouble()).isEqualTo(60.0);
		assertThat(sent.get("explain").asBoolean()).isTrue();
		assertThat(payload).isEqualTo(Fixtures.rotatePayload());
	}

	@Test
	void leavesTheTimestampOutWhenNoneIsGiven() {
		client().recommend(new InferenceRequest("live", null, 35, false));

		assertThat(Fixtures.parse(receivedBody.get()).has("timestamp")).isFalse();
	}

	@Test
	void anUnknownHourIsARejectedRequestCarryingTheApisOwnMessage() {
		status = 404;
		reply = "{\"error\": {\"code\": \"UNKNOWN_HOUR\", \"message\": \"No recorded hour '1999-01-01T00:00:00Z'\"}}";

		assertThatThrownBy(() -> client().recommend(new InferenceRequest("recorded", "1999-01-01T00:00:00Z", 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> {
				assertThat(ex.reason()).isEqualTo(Reason.REJECTED);
				assertThat(ex.getMessage()).isEqualTo("No recorded hour '1999-01-01T00:00:00Z'");
			});
	}

	@Test
	void rateLimitingIsReportedAsSuch() {
		status = 429;
		reply = "{\"error\": {\"code\": \"RATE_LIMITED\", \"message\": \"Too many requests\"}}";

		assertThatThrownBy(() -> client().recommend(new InferenceRequest("live", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> assertThat(ex.reason()).isEqualTo(Reason.RATE_LIMITED));
	}

	@Test
	void aServerErrorIsAnOutageNotACallerMistake() {
		status = 503;
		reply = "<html>upstream down</html>";

		assertThatThrownBy(() -> client().recommend(new InferenceRequest("recorded", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> {
				assertThat(ex.reason()).isEqualTo(Reason.UNAVAILABLE);
				assertThat(ex.getMessage()).isEqualTo("The inference API answered with HTTP 503");
			});
	}

	@Test
	void aReplyThatIsNotJsonIsMalformed() {
		reply = "<html>hello</html>";

		assertThatThrownBy(() -> client().recommend(new InferenceRequest("recorded", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> assertThat(ex.reason()).isEqualTo(Reason.MALFORMED));
	}

	@Test
	void anEmptyReplyIsMalformed() {
		reply = "";

		assertThatThrownBy(() -> client().recommend(new InferenceRequest("recorded", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> assertThat(ex.reason()).isEqualTo(Reason.MALFORMED));
	}

	@Test
	void aReplyThatTakesTooLongIsAnOutage() {
		delayMillis = 1500;

		assertThatThrownBy(() -> client(Duration.ofMillis(300)).recommend(new InferenceRequest("recorded", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> assertThat(ex.reason()).isEqualTo(Reason.UNAVAILABLE));
	}

	@Test
	void anUnreachableServerIsAnOutage() {
		HttpInferenceClient client = client();
		server.stop(0);

		assertThatThrownBy(() -> client.recommend(new InferenceRequest("recorded", null, 35, false)))
			.isInstanceOfSatisfying(InferenceException.class, ex -> assertThat(ex.reason()).isEqualTo(Reason.UNAVAILABLE));
	}
}
