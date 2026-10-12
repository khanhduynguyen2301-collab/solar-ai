-- Decisions returned by the inference API, stored as received. Nothing here is computed by this service
-- except the bookkeeping columns (id, created_at, trigger_source, applied_to_row).
CREATE TABLE decision_record (
    id                              UUID PRIMARY KEY,
    created_at                      TIMESTAMP WITH TIME ZONE NOT NULL,
    trigger_source                  VARCHAR(16)  NOT NULL,
    mode                            VARCHAR(16)  NOT NULL,
    interval_start                  TIMESTAMP WITH TIME ZONE NOT NULL,
    control_target_id               VARCHAR(64)  NOT NULL,
    current_angle_deg               DOUBLE PRECISION NOT NULL,
    action                          VARCHAR(16)  NOT NULL,
    target_angle_deg                DOUBLE PRECISION NOT NULL,
    selected_model                  VARCHAR(32),
    data_status                     VARCHAR(16)  NOT NULL,
    safety_passed                   BOOLEAN      NOT NULL,
    energy_gain_kwh                 DOUBLE PRECISION,
    movement_cost_kwh_equivalent    DOUBLE PRECISION,
    net_benefit_kwh_equivalent      DOUBLE PRECISION,
    reason                          VARCHAR(2000) NOT NULL,
    applied_to_row                  BOOLEAN      NOT NULL,
    payload                         TEXT         NOT NULL
);

CREATE INDEX idx_decision_record_created_at ON decision_record (created_at DESC);
CREATE INDEX idx_decision_record_action ON decision_record (action);

-- SIMULATED row position. There is no hardware: when a decision is applied, the row is recorded
-- as having moved to the decided angle so the next request starts from there.
CREATE TABLE row_state (
    row_id      VARCHAR(64) PRIMARY KEY,
    angle_deg   DOUBLE PRECISION NOT NULL,
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    version     BIGINT NOT NULL
);
