CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  full_name TEXT NOT NULL,
  phone TEXT,
  password_hash TEXT NOT NULL,
  balance_cents BIGINT NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS doctors (
  id SERIAL PRIMARY KEY,
  user_id INT UNIQUE NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  profession TEXT NOT NULL,
  specialty TEXT NOT NULL,
  hpcsa_number TEXT NOT NULL,
  city TEXT NOT NULL,
  address TEXT NOT NULL,
  bio TEXT,
  fee_cents INT NOT NULL CHECK (fee_cents > 0),
  slot_minutes INT NOT NULL DEFAULT 30 CHECK (slot_minutes BETWEEN 10 AND 120),
  status TEXT NOT NULL DEFAULT 'resting' CHECK (status IN ('online','resting','dropin')),
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS availability (
  id SERIAL PRIMARY KEY,
  doctor_id INT NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
  weekday SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
  start_time TIME NOT NULL,
  end_time TIME NOT NULL CHECK (end_time > start_time)
);

CREATE TABLE IF NOT EXISTS appointments (
  id SERIAL PRIMARY KEY,
  patient_id INT NOT NULL REFERENCES users(id),
  doctor_id INT NOT NULL REFERENCES doctors(id),
  starts_at TIMESTAMPTZ NOT NULL,
  ends_at TIMESTAMPTZ NOT NULL,
  fee_cents INT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','completed','cancelled')),
  cancelled_by TEXT,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS appt_slot_uniq ON appointments (doctor_id, starts_at) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS appt_patient_idx ON appointments (patient_id, starts_at DESC);
CREATE INDEX IF NOT EXISTS appt_doctor_idx ON appointments (doctor_id, starts_at);

CREATE TABLE IF NOT EXISTS wallet_tx (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  amount_cents BIGINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('topup','booking','refund','earning')),
  appointment_id INT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wallet_tx_user_idx ON wallet_tx (user_id, id DESC);

CREATE TABLE IF NOT EXISTS topups (
  reference TEXT PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  amount_cents INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviews (
  id SERIAL PRIMARY KEY,
  appointment_id INT UNIQUE NOT NULL REFERENCES appointments(id),
  doctor_id INT NOT NULL REFERENCES doctors(id),
  patient_id INT NOT NULL REFERENCES users(id),
  rating SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  wait_rating SMALLINT CHECK (wait_rating BETWEEN 1 AND 5),
  clinic_rating SMALLINT CHECK (clinic_rating BETWEEN 1 AND 5),
  comm_rating SMALLINT CHECK (comm_rating BETWEEN 1 AND 5),
  comment TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reviews_doctor_idx ON reviews (doctor_id, id DESC);

CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY,
  user_id INT NOT NULL REFERENCES users(id),
  body TEXT NOT NULL,
  link TEXT,
  read BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notif_user_idx ON notifications (user_id, id DESC);
