-- MindGap: database schema (run once in the Supabase SQL editor)
-- Every attempt is a row. A retry points at the gap it answers (attempts.gap_id -> gaps.id).
-- No table stores a name.

create extension if not exists pgcrypto;

create table if not exists learners (
  id          uuid primary key default gen_random_uuid(),
  consent_at  timestamptz not null,
  created_at  timestamptz not null default now()
);

-- Optional account: a nickname (never a real name) and a hashed PIN. Guests leave these empty.
alter table learners add column if not exists handle          text unique;
alter table learners add column if not exists pin_hash        text;
alter table learners add column if not exists failed_attempts int not null default 0;
alter table learners add column if not exists locked_until    timestamptz;

create table if not exists concepts (
  id          text primary key,
  title       text not null,
  sort_order  int  not null default 0
);

create table if not exists cases (
  id          text primary key,
  concept_id  text not null references concepts(id),
  case_text   text not null,
  must_hit    jsonb not null,                    -- [{"id": "h1", "text": "..."}]
  must_not    jsonb not null default '[]'::jsonb, -- [{"id": "n1", "text": "..."}]
  level       text not null check (level in ('first', 'twin', 'after'))
);

create table if not exists sessions (
  id            uuid primary key default gen_random_uuid(),
  learner_id    uuid not null references learners(id),
  concept_id    text not null references concepts(id),
  started_at    timestamptz not null default now(),
  completed_at  timestamptz
);

create table if not exists attempts (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid not null references sessions(id),
  type          text not null check (type in ('explanation', 'retry')),
  learner_text  text not null,
  gap_id        uuid,                            -- set for retries: the proof link
  created_at    timestamptz not null default now()
);

create table if not exists gaps (
  id                  uuid primary key default gen_random_uuid(),
  attempt_id          uuid not null references attempts(id),
  recited_sentence    text not null,
  follow_up_question  text not null,
  pattern             text check (pattern in ('label', 'circular', 'jargon', 'vague_actor', 'definition')),
  created_at          timestamptz not null default now()
);

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'attempts_gap_fk') then
    alter table attempts add constraint attempts_gap_fk foreign key (gap_id) references gaps(id);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'retry_needs_gap') then
    alter table attempts add constraint retry_needs_gap check (type <> 'retry' or gap_id is not null);
  end if;
end $$;

create table if not exists case_results (
  id           uuid primary key default gen_random_uuid(),
  session_id   uuid not null references sessions(id),
  case_id      text not null references cases(id),
  stage        text not null check (stage in ('before', 'after')),
  gap_id       uuid references gaps(id),
  answer_text  text not null,
  evidence     jsonb not null,                   -- AI evidence + code-checked result
  verdict      text not null check (verdict in ('PASS', 'FAIL')),
  created_at   timestamptz not null default now()
);

create index if not exists idx_sessions_learner   on sessions(learner_id);
create index if not exists idx_attempts_session   on attempts(session_id);
create index if not exists idx_gaps_attempt       on gaps(attempt_id);
create index if not exists idx_results_session    on case_results(session_id);

-- Honest feedback from visitors. Read it in Supabase: Table Editor -> feedback.
create table if not exists feedback (
  id          uuid primary key default gen_random_uuid(),
  learner_id  uuid references learners(id),       -- empty if we don't know who
  rating      smallint check (rating between 1 and 5),
  message     text check (char_length(message) <= 2000),
  source      text not null default 'tab' check (source in ('tab', 'session_end')),
  created_at  timestamptz not null default now()
);

-- Only the server (service role key) can read or write. The public anon key gets nothing.
alter table learners      enable row level security;
alter table concepts      enable row level security;
alter table cases         enable row level security;
alter table sessions      enable row level security;
alter table attempts      enable row level security;
alter table gaps          enable row level security;
alter table case_results  enable row level security;
alter table feedback      enable row level security;
