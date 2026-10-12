package ai.solar.control.web;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import ai.solar.control.Fixtures;
import ai.solar.control.decision.DecisionRepository;
import ai.solar.control.inference.InferenceClient;
import ai.solar.control.inference.InferenceClient.InferenceRequest;
import ai.solar.control.inference.InferenceException;
import ai.solar.control.inference.InferenceException.Reason;
import ai.solar.control.row.RowStateRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** The whole service with a real database schema; only the Python inference API is replaced. */
@SpringBootTest
@AutoConfigureMockMvc
class DecisionApiTests {

	@Autowired
	private MockMvc mvc;

	@Autowired
	private DecisionRepository decisions;

	@Autowired
	private RowStateRepository rows;

	@MockitoBean
	private InferenceClient inference;

	@BeforeEach
	void emptyDatabase() {
		decisions.deleteAll();
		rows.deleteAll();
		when(inference.recommend(any())).thenReturn(Fixtures.rotatePayload());
	}

	private ResultActions decide(String body) throws Exception {
		return mvc.perform(post("/api/decisions").contentType(MediaType.APPLICATION_JSON).content(body));
	}

	private InferenceRequest lastRequest(int calls) {
		ArgumentCaptor<InferenceRequest> request = ArgumentCaptor.forClass(InferenceRequest.class);
		verify(inference, org.mockito.Mockito.times(calls)).recommend(request.capture());
		return request.getValue();
	}

	@Test
	void anAppliedDecisionStartsFromTheSimulatedRowAndMovesIt() throws Exception {
		decide("{}").andExpect(status().isCreated())
			.andExpect(header().exists("Location"))
			.andExpect(jsonPath("$.action").value("ROTATE"))
			.andExpect(jsonPath("$.targetAngleDeg").value(30.0))
			.andExpect(jsonPath("$.currentAngleDeg").value(35.0))
			.andExpect(jsonPath("$.appliedToRow").value(true))
			.andExpect(jsonPath("$.trigger").value("MANUAL"))
			.andExpect(jsonPath("$.selectedModel").value("lstm"))
			.andExpect(jsonPath("$.netBenefitKwhEquivalent").value(0.7181979751586914));

		assertThat(lastRequest(1)).isEqualTo(new InferenceRequest("recorded", null, 35.0, false));
		mvc.perform(get("/api/row")).andExpect(jsonPath("$.angleDeg").value(30.0)).andExpect(jsonPath("$.simulated").value(true));
	}

	@Test
	void theNextAppliedDecisionStartsWhereTheLastOneLeftTheRow() throws Exception {
		decide("{}");
		decide("{\"mode\": \"live\"}");

		assertThat(lastRequest(2)).isEqualTo(new InferenceRequest("live", null, 30.0, false));
	}

	@Test
	void aWhatIfQuestionIsStoredButLeavesTheRowWhereItIs() throws Exception {
		decide("{\"currentAngleDeg\": 60, \"timestamp\": \"2025-06-05T19:00:00Z\", \"explain\": true}").andExpect(status().isCreated())
			.andExpect(jsonPath("$.appliedToRow").value(false))
			.andExpect(jsonPath("$.currentAngleDeg").value(60.0));

		assertThat(lastRequest(1)).isEqualTo(new InferenceRequest("recorded", "2025-06-05T19:00:00Z", 60.0, true));
		mvc.perform(get("/api/row")).andExpect(jsonPath("$.angleDeg").value(35.0));
		assertThat(decisions.count()).isEqualTo(1);
	}

	@Test
	void aHoldLeavesTheRowWhereItIs() throws Exception {
		when(inference.recommend(any())).thenReturn(Fixtures.holdPayload(35));

		decide("{}").andExpect(jsonPath("$.action").value("HOLD")).andExpect(jsonPath("$.appliedToRow").value(true));

		mvc.perform(get("/api/row")).andExpect(jsonPath("$.angleDeg").value(35.0));
	}

	@Test
	void aRequestWithNoBodyUsesTheDefaults() throws Exception {
		mvc.perform(post("/api/decisions")).andExpect(status().isCreated());

		assertThat(lastRequest(1)).isEqualTo(new InferenceRequest("recorded", null, 35.0, false));
	}

	@Test
	void theStoredPayloadIsExactlyWhatTheInferenceApiReturned() throws Exception {
		decide("{}");

		String stored = mvc.perform(get("/api/decisions/latest/payload"))
			.andExpect(status().isOk())
			.andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
			.andReturn()
			.getResponse()
			.getContentAsString();
		assertThat(Fixtures.parse(stored)).isEqualTo(Fixtures.rotatePayload());
	}

	@Test
	void historyIsNewestFirstAndCanBeFilteredAndPaged() throws Exception {
		decide("{}");
		when(inference.recommend(any())).thenReturn(Fixtures.holdPayload(30));
		decide("{}");
		decide("{}");

		mvc.perform(get("/api/decisions"))
			.andExpect(jsonPath("$.totalItems").value(3))
			.andExpect(jsonPath("$.items[0].action").value("HOLD"))
			.andExpect(jsonPath("$.items[2].action").value("ROTATE"));
		mvc.perform(get("/api/decisions").param("action", "ROTATE"))
			.andExpect(jsonPath("$.totalItems").value(1))
			.andExpect(jsonPath("$.items[0].action").value("ROTATE"));
		mvc.perform(get("/api/decisions").param("size", "2").param("page", "1"))
			.andExpect(jsonPath("$.items.length()").value(1))
			.andExpect(jsonPath("$.totalPages").value(2));
		mvc.perform(get("/api/decisions").param("from", "2999-01-01T00:00:00Z")).andExpect(jsonPath("$.totalItems").value(0));
	}

	@Test
	void aStoredDecisionCanBeFetchedByIdWithItsPayload() throws Exception {
		String location = decide("{}").andReturn().getResponse().getHeader("Location");

		mvc.perform(get(location)).andExpect(status().isOk()).andExpect(jsonPath("$.action").value("ROTATE"));
		mvc.perform(get(location + "/payload")).andExpect(status().isOk()).andExpect(jsonPath("$.decision.action").value("ROTATE"));
	}

	@Test
	void statsCountActionsAndSumOnlyAppliedRotations() throws Exception {
		decide("{}");
		decide("{\"currentAngleDeg\": 60}");
		when(inference.recommend(any())).thenReturn(Fixtures.holdPayload(30));
		decide("{}");

		mvc.perform(get("/api/stats"))
			.andExpect(jsonPath("$.total").value(3))
			.andExpect(jsonPath("$.byAction.ROTATE").value(2))
			.andExpect(jsonPath("$.byAction.HOLD").value(1))
			// One applied ROTATE; the what-if ROTATE and the HOLD are not counted.
			.andExpect(jsonPath("$.netBenefitOfAppliedRotationsKwhEquivalent").value(0.7181979751586914));
	}

	@Test
	void statsReportNoSumRatherThanZeroWhenNothingWasApplied() throws Exception {
		mvc.perform(get("/api/stats"))
			.andExpect(jsonPath("$.total").value(0))
			.andExpect(jsonPath("$.netBenefitOfAppliedRotationsKwhEquivalent").doesNotExist())
			.andExpect(jsonPath("$.first").doesNotExist());
	}

	@Test
	void anEmptyHistoryHasNoLatestDecision() throws Exception {
		mvc.perform(get("/api/decisions/latest")).andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("NOT_FOUND"));
		mvc.perform(get("/api/decisions/latest/payload")).andExpect(status().isNotFound());
		mvc.perform(get("/api/decisions/00000000-0000-0000-0000-000000000000")).andExpect(status().isNotFound());
	}

	@Test
	void invalidRequestsAreRejectedBeforeTheInferenceApiIsCalled() throws Exception {
		decide("{\"currentAngleDeg\": 500}").andExpect(status().isUnprocessableEntity())
			.andExpect(jsonPath("$.error.code").value("INVALID_REQUEST"))
			.andExpect(jsonPath("$.error.message").value(org.hamcrest.Matchers.containsString("currentAngleDeg")));
		decide("{\"mode\": \"forecast\"}").andExpect(status().isUnprocessableEntity());
		decide("{not json").andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.error.code").value("INVALID_REQUEST"));
		mvc.perform(get("/api/decisions").param("size", "5000")).andExpect(status().isUnprocessableEntity());
		mvc.perform(get("/api/decisions").param("action", "SPIN")).andExpect(status().isUnprocessableEntity());
		mvc.perform(get("/api/decisions/not-a-uuid")).andExpect(status().isUnprocessableEntity());

		verifyNoInteractions(inference);
		assertThat(decisions.count()).isZero();
	}

	@Test
	void aRequestTheInferenceApiRejectsIsReportedWithItsMessage() throws Exception {
		when(inference.recommend(any())).thenThrow(new InferenceException(Reason.REJECTED, "No recorded hour '1999-01-01T00:00:00Z'"));

		decide("{\"timestamp\": \"1999-01-01T00:00:00Z\"}").andExpect(status().isUnprocessableEntity())
			.andExpect(jsonPath("$.error.code").value("REQUEST_REJECTED"))
			.andExpect(jsonPath("$.error.message").value("No recorded hour '1999-01-01T00:00:00Z'"));
		assertThat(decisions.count()).isZero();
	}

	@Test
	void anInferenceOutageStoresNothingAndLeavesTheRowAlone() throws Exception {
		when(inference.recommend(any())).thenThrow(new InferenceException(Reason.UNAVAILABLE, "connection refused to 10.0.0.5:8000"));

		decide("{}").andExpect(status().isServiceUnavailable())
			.andExpect(jsonPath("$.error.code").value("INFERENCE_UNAVAILABLE"))
			.andExpect(jsonPath("$.error.message").value(org.hamcrest.Matchers.not(org.hamcrest.Matchers.containsString("10.0.0.5"))));

		assertThat(decisions.count()).isZero();
		mvc.perform(get("/api/row")).andExpect(jsonPath("$.angleDeg").value(35.0));
	}

	@Test
	void anUnusablePayloadIsRefusedAndNotStored() throws Exception {
		var payload = Fixtures.rotatePayload();
		payload.remove("decision");
		when(inference.recommend(any())).thenReturn(payload);

		decide("{}").andExpect(status().isBadGateway()).andExpect(jsonPath("$.error.code").value("INFERENCE_INVALID"));

		assertThat(decisions.count()).isZero();
		mvc.perform(get("/api/row")).andExpect(jsonPath("$.angleDeg").value(35.0));
	}

	@Test
	void rateLimitingByTheInferenceApiIsReportedAsBusy() throws Exception {
		when(inference.recommend(any())).thenThrow(new InferenceException(Reason.RATE_LIMITED, "Too many requests"));

		decide("{}").andExpect(status().isServiceUnavailable()).andExpect(jsonPath("$.error.code").value("INFERENCE_BUSY"));
	}

	@Test
	void unknownRoutesAndWrongMethodsUseTheSameErrorShape() throws Exception {
		mvc.perform(get("/api/nowhere")).andExpect(status().isNotFound()).andExpect(jsonPath("$.error.code").value("HTTP_ERROR"));
		mvc.perform(post("/api/stats")).andExpect(status().isMethodNotAllowed()).andExpect(jsonPath("$.error.code").value("HTTP_ERROR"));
	}
}
