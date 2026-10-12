package ai.solar.control.config;

import jakarta.validation.Valid;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/** Settings under {@code solar.*}. Invalid settings stop the service at startup. */
@Validated
@ConfigurationProperties("solar")
public record SolarProperties(@Valid @NotNull Inference inference, @Valid @NotNull Row row, @Valid @NotNull Schedule schedule) {

	/** Where the Python inference API is, how long to wait for it, and the key it expects (blank when it expects none). */
	public record Inference(@NotNull URI baseUrl, @NotNull Duration connectTimeout, @NotNull Duration readTimeout, String apiKey) {

		public boolean hasApiKey() {
			return apiKey != null && !apiKey.isBlank();
		}

		/** The key is left out, so logging these settings can never print it. */
		@Override
		public String toString() {
			return "Inference[baseUrl=" + baseUrl + ", connectTimeout=" + connectTimeout + ", readTimeout=" + readTimeout
					+ ", apiKey=" + (hasApiKey() ? "<set>" : "<none>") + "]";
		}
	}

	/** The controlled row and the angle its simulated position starts at. */
	public record Row(@NotBlank String id, @DecimalMin("0") @DecimalMax("90") double initialAngleDeg) {
	}

	/** Automatic decisions. {@code mode} is passed to the inference API as given. */
	public record Schedule(boolean enabled, @NotBlank String cron, @Pattern(regexp = "recorded|live") String mode) {
	}
}
