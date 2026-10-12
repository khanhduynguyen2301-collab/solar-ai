# Control service image: the Spring Boot service that requests, stores and tracks decisions.
#
#   docker build -t solar-ai-control-service control-service
#   docker run --rm -p 8080:8080 \
#     -e SOLAR_INFERENCE_URL=http://host.docker.internal:8000 \
#     -e SOLAR_DB_URL=jdbc:postgresql://host.docker.internal:5432/solar_farm_ai \
#     -e SOLAR_DB_USER=solar_dev -e SOLAR_DB_PASSWORD=... \
#     solar-ai-control-service
#
# The image holds only the application jar and a Java runtime. It contains no database
# password and no data; the inference API address and the database are given at run time.

# Build stage: compiles and packages with the same Maven wrapper developers and CI use.
# Tests are skipped here because the CI test job has already run them on this commit.
FROM eclipse-temurin:21-jdk AS build
WORKDIR /build
COPY .mvn ./.mvn
COPY mvnw pom.xml ./
COPY src ./src
RUN chmod +x mvnw && ./mvnw --batch-mode -DskipTests package

FROM eclipse-temurin:21-jre

# Run as an unprivileged user. /data is for the "local" profile's file database.
RUN useradd --create-home --uid 10001 solar \
    && mkdir -p /app /data \
    && chown solar /data
WORKDIR /data
COPY --from=build /build/target/control-service-*.jar /app/app.jar
USER solar
VOLUME ["/data"]

EXPOSE 8080
# Plain bash, so the image needs no HTTP client: healthy only when the service reports UP.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD bash -c 'exec 3<>/dev/tcp/127.0.0.1/8080 && printf "GET /actuator/health HTTP/1.0\r\n\r\n" >&3 && grep -q "\"status\":\"UP\"" <&3'

ENTRYPOINT ["java", "-XX:MaxRAMPercentage=75", "-jar", "/app/app.jar"]
