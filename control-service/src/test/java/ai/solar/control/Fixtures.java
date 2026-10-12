package ai.solar.control;

import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/** Real inference payloads for tests. The base one is a decision the Python API actually produced. */
public final class Fixtures {

	private static final JsonMapper MAPPER = JsonMapper.builder().build();

	private Fixtures() {
	}

	/** The recorded demo run: LSTM selected, ROTATE from 60 to 30 degrees. */
	public static String rotatePayloadText() {
		try (InputStream stream = Fixtures.class.getResourceAsStream("/recommendation-rotate.json")) {
			return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
		}
		catch (IOException ex) {
			throw new UncheckedIOException(ex);
		}
	}

	public static ObjectNode rotatePayload() {
		return (ObjectNode) MAPPER.readTree(rotatePayloadText());
	}

	/** The same run as a HOLD at {@code angleDeg}, shaped the way the inference API reports one. */
	public static ObjectNode holdPayload(double angleDeg) {
		ObjectNode payload = rotatePayload();
		ObjectNode decision = (ObjectNode) payload.get("decision");
		decision.put("action", "HOLD");
		decision.put("target_angle_deg", angleDeg);
		decision.put("reason", "Holding: best net benefit does not exceed the threshold.");
		return payload;
	}

	public static JsonNode parse(String json) {
		return MAPPER.readTree(json);
	}
}
