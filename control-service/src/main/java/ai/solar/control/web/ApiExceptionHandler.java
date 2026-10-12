package ai.solar.control.web;

import ai.solar.control.inference.InferenceException;
import jakarta.validation.ConstraintViolationException;
import java.util.Map;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * Every error leaves in one shape, the same one the inference API uses:
 * {@code {"error": {"code": "...", "message": "..."}}}. Internal details go to the log only.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

	private static final Logger LOGGER = LoggerFactory.getLogger(ApiExceptionHandler.class);

	/** The error envelope. */
	public record ApiError(Map<String, String> error) {

		static ApiError of(String code, String message) {
			return new ApiError(Map.of("code", code, "message", message));
		}
	}

	private static ResponseEntity<ApiError> respond(HttpStatus status, String code, String message) {
		return ResponseEntity.status(status).body(ApiError.of(code, message));
	}

	@ExceptionHandler(MethodArgumentNotValidException.class)
	ResponseEntity<ApiError> invalidBody(MethodArgumentNotValidException ex) {
		String problems = ex.getBindingResult().getFieldErrors().stream()
			.map(error -> error.getField() + ": " + error.getDefaultMessage())
			.sorted()
			.collect(Collectors.joining("; "));
		return respond(HttpStatus.UNPROCESSABLE_ENTITY, "INVALID_REQUEST", problems);
	}

	@ExceptionHandler({ ConstraintViolationException.class, HandlerMethodValidationException.class, MethodArgumentTypeMismatchException.class })
	ResponseEntity<ApiError> invalidParameter(Exception ex) {
		return respond(HttpStatus.UNPROCESSABLE_ENTITY, "INVALID_REQUEST", "A request parameter is missing, malformed or out of range");
	}

	@ExceptionHandler(HttpMessageNotReadableException.class)
	ResponseEntity<ApiError> unreadableBody(HttpMessageNotReadableException ex) {
		return respond(HttpStatus.UNPROCESSABLE_ENTITY, "INVALID_REQUEST", "The request body is not valid JSON for this endpoint");
	}

	@ExceptionHandler(NotFoundException.class)
	ResponseEntity<ApiError> notFound(NotFoundException ex) {
		return respond(HttpStatus.NOT_FOUND, "NOT_FOUND", ex.getMessage());
	}

	@ExceptionHandler(InferenceException.class)
	ResponseEntity<ApiError> inference(InferenceException ex) {
		return switch (ex.reason()) {
			// The inference API's own message explains what was wrong with the request.
			case REJECTED -> respond(HttpStatus.UNPROCESSABLE_ENTITY, "REQUEST_REJECTED", ex.getMessage());
			case RATE_LIMITED -> respond(HttpStatus.SERVICE_UNAVAILABLE, "INFERENCE_BUSY", "The inference API is rate limiting requests; try again shortly");
			case UNAVAILABLE -> {
				LOGGER.warn("Inference API unavailable: {}", ex.getMessage());
				yield respond(HttpStatus.SERVICE_UNAVAILABLE, "INFERENCE_UNAVAILABLE", "The inference API is not available; no decision was made");
			}
			case MALFORMED -> {
				LOGGER.error("Inference API returned an unusable reply: {}", ex.getMessage());
				yield respond(HttpStatus.BAD_GATEWAY, "INFERENCE_INVALID", "The inference API returned an unusable reply; nothing was stored");
			}
		};
	}

	@ExceptionHandler(Exception.class)
	ResponseEntity<ApiError> unexpected(Exception ex) {
		// Framework errors that already carry a status: unknown route, wrong method, unsupported media type.
		if (ex instanceof ErrorResponse framework) {
			HttpStatus status = HttpStatus.valueOf(framework.getStatusCode().value());
			return respond(status, "HTTP_ERROR", status.getReasonPhrase());
		}
		LOGGER.error("Unhandled error while serving a request", ex);
		return respond(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "The server could not complete the request");
	}
}
