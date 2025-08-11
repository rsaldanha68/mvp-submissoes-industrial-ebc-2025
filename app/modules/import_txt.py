import re
from datetime import datetime
from sqlalchemy import text

# Extrai RA e nomes de relatórios TXT do SharePoint/PUC
RA_LINE = re.compile(r"\b(RA\d{8})\b\s+([^\n\r]+)")

def _read_text_any(file_path: str) -> str:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # fallback binário
    return open(file_path, "rb").read().decode("latin-1", "ignore")

def parse_puc_txt(file_path: str):
    """Retorna metadados e uma lista [(ra, name)].
    Campos: turma, disciplina (string simples), professor, cod_professor, students
    """
    txt = _read_text_any(file_path).replace("\r\n", "\n")
    turma = None
    m_turma = re.search(r"\n(ECO-[A-Z0-9]+)", txt)
    if m_turma:
        turma = m_turma.group(1).strip().replace("ECO-", "")
    prof = ""
    m_prof = re.search(r"Professor\s+(.+?)\s+\n", txt)
    if m_prof:
        prof = m_prof.group(1).strip()
    m_cod = re.search(r"C[óo]d\.Usu[áa]rio:\s*(\d+)", txt)
    cod_prof = m_cod.group(1) if m_cod else ""
    disciplina = "ECONOMIA INDUSTRIAL" if "INDUSTRIAL" in txt.upper() else ("EBC II" if "BRASILEIRA" in txt.upper() else "")

    students = []
    for m in RA_LINE.finditer(txt):
        ra = m.group(1).strip()
        name = re.sub(r"\s{2,}", " ", m.group(2).strip())
        students.append((ra, name))
    return {
        "turma": turma, "disciplina": disciplina,
        "professor": prof, "cod_professor": cod_prof,
        "students": students
    }

def upsert_students_and_enroll(engine, term: str, disciplina_code: str, turma: str, students: list):
    """Garante offering (disciplina+term+turma), insere/reativa alunos e matrículas."""
    with engine.begin() as conn:
        # disciplina
        did = conn.execute(text("SELECT id FROM disciplines WHERE code=:c"), {"c": disciplina_code}).scalar()
        if not did:
            conn.execute(text("INSERT INTO disciplines(code,name) VALUES(:c,:n)"),
                         {"c": disciplina_code, "n": "Economia Industrial" if disciplina_code=="IND" else "Economia Brasileira II"})
            did = conn.execute(text("SELECT id FROM disciplines WHERE code=:c"), {"c": disciplina_code}).scalar()
        # offering
        oid = conn.execute(text("""SELECT id FROM offerings WHERE discipline_id=:d AND term=:t AND turma=:u"""),
                           {"d": did, "t": term, "u": turma}).scalar()
        if not oid:
            conn.execute(text("""INSERT INTO offerings(discipline_id,term,turma) VALUES(:d,:t,:u)"""),
                         {"d": did, "t": term, "u": turma})
            oid = conn.execute(text("SELECT id FROM offerings WHERE discipline_id=:d AND term=:t AND turma=:u"),
                               {"d": did, "t": term, "u": turma}).scalar()
        # alunos + matrícula
        for ra, name in students:
            sid = conn.execute(text("SELECT id FROM students WHERE ra=:ra"), {"ra": ra}).scalar()
            if not sid:
                conn.execute(text("INSERT INTO students(ra,name,turma,active) VALUES(:ra,:n,:tu,1)"),
                             {"ra": ra, "n": name, "tu": turma})
                sid = conn.execute(text("SELECT id FROM students WHERE ra=:ra"), {"ra": ra}).scalar()
            eid = conn.execute(text("SELECT id FROM enrollments WHERE student_id=:s AND offering_id=:o"),
                               {"s": sid, "o": oid}).scalar()
            if not eid:
                conn.execute(text("INSERT INTO enrollments(student_id,offering_id,active) VALUES(:s,:o,1)"),
                             {"s": sid, "o": oid})
            else:
                conn.execute(text("UPDATE enrollments SET active=1 WHERE id=:e"), {"e": eid})
    return True
