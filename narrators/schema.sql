-- Narrator metadata database. Every stage creates its tables through this file
-- (CREATE TABLE IF NOT EXISTS) and clears its own tables before rewriting them.

-- ---------------------------------------------------------------- stage 1: Noor
create table if not exists ravi (
  ravi_id          integer primary key,
  name             text,            -- first field of the most frequent Hint, else اشتهار, else slot title
  name_key         text,            -- norm_key(name)
  mazhab           text,            -- Hint field 2 (امامی / معصوم / ...)
  grade            text,            -- Hint field 3 (ثقة / مجهول / ...)
  kunya            text,
  laqab_json       text,            -- [{text, books:[...]}]
  ishtihar         text,
  tabaqa_json      text,            -- [{text, books}] : Imam(s) the narrator transmitted from
  wafat_json       text,            -- [{text, books}] death-year statements (word form)
  wiladat_json     text,
  eval_result      text,            -- نتيجه ارزيابي
  eval_summary     text,            -- جمع بندي ارزيابي
  jarh_json        text,            -- [{text, books:[{book, ravi_title, url}]}]
  tawthiq          text,            -- توثيقات عام
  mazhab_info      text,            -- مذهب
  extra_json       text,            -- any other InfoList block
  n_slots          integer default 0,   -- slots naming this id (ambiguous ones count fractionally)
  n_slots_unambig  integer default 0,
  n_hadith         integer default 0,
  n_hints          integer default 0
);
create index if not exists ravi_name_key on ravi(name_key);

create table if not exists citation (
  ravi_id   integer not null,
  book_raw  text not null,          -- label as printed by Noor, e.g. «رجال النجاشي»
  book      text,                   -- canonical key from data/books.json, or null
  vol       integer,
  page      integer,
  context   text,                   -- InfoList block title the citation came from
  text      text,                   -- the statement cited (e.g. «ثقة»)
  ravi_title text,                  -- the name under which the book lists him
  url       text,
  primary key (ravi_id, book_raw, vol, page, context, text)
);
create index if not exists citation_book_page on citation(book, vol, page);

create table if not exists hadith (
  hadith_id   integer primary key,
  book        text,
  group_id    integer,
  century     integer,
  n_sanads    integer
);

create table if not exists slot (
  hadith_id  integer not null,
  sanad_idx  integer not null,
  pos        integer not null,      -- 0 = collector side, ascending toward the Infallible
  order_no   integer,
  title      text,                  -- the name as written in the isnād
  type       integer,
  term       text,                  -- transmission word before this name (عن، حدثنا، ...)
  ravi_id    integer,               -- null when ambiguous or unresolved
  cand_json  text not null,         -- JSON list of candidate RaviIds (may be empty)
  primary key (hadith_id, sanad_idx, pos)
);
create index if not exists slot_ravi on slot(ravi_id);

create table if not exists adjacency (
  teacher   integer not null,
  student   integer not null,
  n_exact   integer default 0,      -- both slots unambiguous
  w_amb     real default 0,         -- Σ 1/(|Cu|·|Cv|) over ambiguous pairs
  n_hadith  integer default 0,      -- distinct hadiths (exact pairs)
  primary key (teacher, student)
);
create index if not exists adjacency_student on adjacency(student);

create table if not exists noor_book (
  book        text primary key,
  n_hadith    integer,
  n_with_sanad integer,
  n_slots     integer
);

create table if not exists book_coverage (
  book         text primary key,
  n_ravi       integer,
  n_slots      integer,
  n_entries    integer,
  same_edition integer
);

-- ------------------------------------------------------------- stage 2: rijāl texts
create table if not exists entry (
  entry_id    integer primary key autoincrement,
  book        text not null,
  entry_no    integer,
  vol         integer,
  page_start  integer,
  page_end    integer,
  section     text,
  subsection  text,
  imam        text,                 -- Tusi Rijal: canonical Imam key of the section
  heading     text,
  heading_key text,
  body        text,
  line_no     integer,
  unique (book, entry_no, line_no)
);
create index if not exists entry_book_page on entry(book, page_start, page_end);
create index if not exists entry_heading_key on entry(book, heading_key);

create table if not exists page_text (
  book  text not null,
  vol   integer,
  page  integer,
  text  text,
  primary key (book, vol, page)
);

create table if not exists wikishia (
  page_id       integer not null,
  lang          text not null,
  title         text,
  template      text,
  name_full     text,
  kunya         text,
  laqab         text,
  birthplace    text,
  residence     text,
  death_text    text,
  death_year    integer,
  imams_json    text,
  teachers_json text,
  students_json text,
  grade         text,
  interwiki_json text,
  raw_wikitext  text,
  primary key (lang, page_id)
);

create table if not exists thaqalayn_narrator (
  tq_id        integer primary key,
  name_ar      text,
  name_en      text,
  n_narrations integer
);
create table if not exists thaqalayn_adjacency (
  teacher integer not null,
  student integer not null,
  n       integer default 0,
  primary key (teacher, student)
);

-- --------------------------------------------------------------- stage 3: links
create table if not exists link (
  ravi_id     integer not null,
  target_kind text not null,        -- entry | wikishia | thaqalayn | kg | hawramani | muslimscholars | arsanad | infallible
  target_id   text not null,
  method      text,                 -- fixed | cited | cited_pm1 | cited_pm2 | alpha | name | quote | sunni | sunni_er
  confidence  text,                 -- high | medium | low
  score       real,
  name_score  real,
  ctx         integer,
  detail_json text,
  primary key (ravi_id, target_kind, target_id)
);
create index if not exists link_target on link(target_kind, target_id);

-- --------------------------------------------------------------- stage 4: facts
create table if not exists fact (
  fact_id   integer primary key autoincrement,
  ravi_id   integer not null,
  kind      text not null,          -- death | death_interval | city | imam | birth
  value     text,
  lo        real,
  hi        real,
  source    text,                   -- noor | book key | wikishia | kg | ...
  entry_id  integer,
  evidence  text,
  pattern   text,
  priority  integer
);
create index if not exists fact_ravi on fact(ravi_id, kind);

create table if not exists city (
  canonical   text primary key,
  thurayya_id text,
  lat         real,
  lon         real
);
create table if not exists city_alias (
  alias_key text primary key,
  canonical text not null,
  kind      text                    -- nisba | toponym
);

create table if not exists infallible (
  ravi_id   integer primary key,
  canonical text,
  idx       integer,                -- 0 = the Prophet ... 13 = the Mahdi; 14 = Fāṭima
  death     integer,
  reign_lo  integer,
  reign_hi  integer,
  city      text
);
