*** /dev/null
--- a/sql/2025_02_migration.sql
@@
+-- Semestre 2025/2 – base de dados para alunos/disciplinas/ofertas/matrículas/avaliações
+PRAGMA foreign_keys=ON;
+
+CREATE TABLE IF NOT EXISTS students (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  ra TEXT UNIQUE NOT NULL,
+  name TEXT NOT NULL,
+  email TEXT,
+  turma TEXT,
+  active INTEGER DEFAULT 1
+);
+
+CREATE TABLE IF NOT EXISTS instructors (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  code TEXT UNIQUE,
+  name TEXT NOT NULL,
+  email TEXT
+);
+
+CREATE TABLE IF NOT EXISTS disciplines (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  code TEXT UNIQUE,         -- 'IND' | 'EBCII'
+  name TEXT NOT NULL
+);
+
+CREATE TABLE IF NOT EXISTS semesters (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  term TEXT UNIQUE NOT NULL -- '2025/2'
+);
+
+CREATE TABLE IF NOT EXISTS offerings (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  discipline_id INTEGER NOT NULL,
+  term TEXT NOT NULL,       -- '2025/2'
+  turma TEXT NOT NULL,      -- 'MA6','MB6','NA6','NB6',...
+  instructor_id INTEGER,
+  UNIQUE (discipline_id, term, turma)
+);
+
+-- Matrículas: aluno pode estar em IND, EBCII ou ambos
+CREATE TABLE IF NOT EXISTS enrollments (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  student_id INTEGER NOT NULL,
+  offering_id INTEGER NOT NULL,
+  active INTEGER DEFAULT 1,
+  UNIQUE (student_id, offering_id)
+);
+
+-- Avaliações por docentes (likes / nota)
+CREATE TABLE IF NOT EXISTS reviews (
+  id INTEGER PRIMARY KEY AUTOINCREMENT,
+  submission_id INTEGER NOT NULL,
+  instructor_id INTEGER NOT NULL,
+  score REAL,               -- 0..10
+  liked INTEGER DEFAULT 0,
+  created_at TEXT,
+  UNIQUE (submission_id, instructor_id)
+);
+
+-- Ajuste opcional: grupos com referência à disciplina-oferta (se desejar)
+-- ALTER TABLE groups ADD COLUMN offering_id INTEGER;
+
+-- Seeds mínimos
+INSERT OR IGNORE INTO disciplines(code,name) VALUES
+  ('IND','Economia Industrial'),
+  ('EBCII','Economia Brasileira II');
+
+INSERT OR IGNORE INTO semesters(term) VALUES ('2025/2');
