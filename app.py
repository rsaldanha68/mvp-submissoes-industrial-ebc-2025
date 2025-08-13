# -*- coding: utf-8 -*-
import os, sys, json, pathlib, re
from datetime import datetime
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

# =========================================================
# CONFIG BÁSICA
# =========================================================
st.set_page_config(page_title="Submissões – Industrial & EBC II (2º/2025)", layout="wide")

# Pastas
DATA_DIR   = "data"
UPLOAD_DIR = "uploads"
PUBLIC_DIR = "public"
for p in (DATA_DIR, UPLOAD_DIR, PUBLIC_DIR, "modules"):
    os.makedirs(p, exist_ok=True)

# Banco de dados (SQLite)
DB_PATH = os.path.join(DATA_DIR, "app.db")
engine  = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)

# Admin por secrets/env (defaults prontos)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", st.secrets.get("ADMIN_EMAIL", "rsaldanha@pucsp.br"))
ADMIN_PIN   = os.environ.get("ADMIN_PIN",   st.secrets.get("ADMIN_PIN",   "8722"))

# Parâmetros/config
THEMES_JSON         = os.path.join(DATA_DIR, "themes_2025_2.json")
DEFAULT_DEADLINE    = "2025-10-15T23:59:59"  # data-limite padrão para reservar tema
DEFAULT_MIN_AFTER   = 3                      # mínimo após a data-limite
DEFAULT_MIN_BEFORE  = 5                      # mínimo antes da data-limite

# =========================================================
# SCHEMA / MIGRAÇÃO
# =========================================================
with engine.begin() as conn:
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS students(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ra TEXT UNIQUE,
        name TEXT NOT NULL,
        email TEXT,
        turma TEXT,
        active INTEGER DEFAULT 1
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS professors(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT CHECK (role IN ('admin','docente')) DEFAULT 'docente',
        pin TEXT,
        approved INTEGER DEFAULT 0
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS groups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE,
        turma TEXT,
        created_by TEXT,
        created_at TEXT
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS group_members(
        group_id INTEGER,
        student_ra TEXT,
        UNIQUE(group_id, student_ra)
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS themes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER UNIQUE,
        title TEXT,
        category TEXT,
        status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
        reserved_by TEXT,
        reserved_at TEXT
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_code TEXT,
        theme_title TEXT,
        report_path TEXT,
        slides_path TEXT,
        zip_path TEXT,
        video_link TEXT,
        consent INTEGER DEFAULT 0,
        submitted_by TEXT,
        submitted_at TEXT,
        approved INTEGER DEFAULT 0
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS reviews(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER,
        instructor_id INTEGER,
        score REAL,
        liked INTEGER DEFAULT 0,
        created_at TEXT,
        UNIQUE(submission_id, instructor_id)
    );""")
    conn.exec_driver_sql("""
    CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY,
        value TEXT
    );""")

    # Seeds de config
    if not conn.execute(text("SELECT 1 FROM config WHERE key='theme_reserve_deadline'")).fetchone():
        conn.execute(text("INSERT INTO config(key,value) VALUES('theme_reserve_deadline', :v)"),
                     {"v": DEFAULT_DEADLINE})
    if not conn.execute(text("SELECT 1 FROM config WHERE key='theme_min_before'")).fetchone():
        conn.execute(text("INSERT INTO config(key,value) VALUES('theme_min_before', :v)"),
                     {"v": str(DEFAULT_MIN_BEFORE)})
    if not conn.execute(text("SELECT 1 FROM config WHERE key='theme_min_after'")).fetchone():
        conn.execute(text("INSERT INTO config(key,value) VALUES('theme_min_after', :v)"),
                     {"v": str(DEFAULT_MIN_AFTER)})

    # Seed do admin (não sobrescreve, só cria se não existir)
    conn.execute(text("""
        INSERT INTO professors (name, email, role, pin, approved)
        SELECT :n, :e, 'admin', :p, 1
        WHERE NOT EXISTS (SELECT 1 FROM professors WHERE LOWER(email)=LOWER(:e))
    """), {"n": "Administrador", "e": ADMIN_EMAIL, "p": ADMIN_PIN})

# =========================================================
# THEMES: carga inicial (merge por number)
# =========================================================
def load_themes_from_json(path_json: str) -> int:
    if not os.path.exists(path_json):
        return 0
    try:
        with open(path_json, "r", encoding="utf-8") as f:
            items = json.load(f) or []
    except Exception:
        return 0
    inserted = 0
    with engine.begin() as conn:
        have_nums = {r[0] for r in conn.execute(text("SELECT number FROM themes WHERE number IS NOT NULL")).fetchall()}
        for it in items:
            if isinstance(it, dict):
                num = it.get("number")
                title = it.get("title", "").strip()
                cat = it.get("category", "").strip()
            else:
                num = None
                title = str(it).strip()
                cat = "Outro"
            if not title or (num is not None and num in have_nums):
                continue
            conn.execute(text("""
                INSERT INTO themes(number,title,category,status)
                VALUES(:n,:t,:c,'livre')
            """), {"n": num, "t": title, "c": cat})
            inserted += 1
    return inserted

_added = load_themes_from_json(THEMES_JSON)
if _added:
    st.sidebar.success(f"Temas adicionados: +{_added}")

# =========================================================
# HELPERS
# =========================================================
def get_df(sql: str, params=None):
    params = params or {}
    with engine.begin() as conn:
        return pd.read_sql(text(sql), conn, params=params)

def run_sql(sql: str, params=None):
    params = params or {}
    with engine.begin() as conn:
        conn.execute(text(sql), params)

def next_group_code_for_turma(turma: str) -> str:
    df = get_df("SELECT code FROM groups WHERE turma=:t", {"t": turma})
    max_n = 0
    for code in df["code"].tolist():
        m = re.match(rf"^{re.escape(turma)}G(\d+)$", code or "", re.IGNORECASE)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{turma}G{max_n+1}"

def student_group(student_ra: str):
    df = get_df("""
        SELECT g.id, g.code, g.turma
        FROM groups g JOIN group_members gm ON g.id=gm.group_id
        WHERE gm.student_ra=:ra
    """, {"ra": student_ra})
    return None if df.empty else df.iloc[0].to_dict()

def group_member_count(group_id: int) -> int:
    return int(get_df("SELECT COUNT(*) AS c FROM group_members WHERE group_id=:g", {"g": group_id}).iloc[0]["c"])

def list_unassigned_students():
    return get_df("""
        SELECT s.ra, s.name, s.turma
        FROM students s
        WHERE s.active=1
          AND NOT EXISTS (SELECT 1 FROM group_members gm WHERE gm.student_ra=s.ra)
        ORDER BY s.turma, s.name
    """)

def list_free_themes(category: str | None = None):
    if category and category != "Todos":
        return get_df("""
            SELECT number,title FROM themes
            WHERE status='livre' AND category=:c
            ORDER BY COALESCE(number,9999), title
        """, {"c": category})
    return get_df("""
        SELECT number,title FROM themes
        WHERE status='livre'
        ORDER BY COALESCE(number,9999), title
    """)

def current_rules():
    cfg = get_df("SELECT key,value FROM config WHERE key IN ('theme_reserve_deadline','theme_min_before','theme_min_after')")
    d = {r["key"]: r["value"] for _, r in cfg.iterrows()}
    try:
        deadline = datetime.strptime(d.get("theme_reserve_deadline", DEFAULT_DEADLINE), "%Y-%m-%dT%H:%M:%S")
    except Exception:
        deadline = datetime.strptime(DEFAULT_DEADLINE, "%Y-%m-%dT%H:%M:%S")
    try:
        min_before = int(d.get("theme_min_before", str(DEFAULT_MIN_BEFORE)))
    except Exception:
        min_before = DEFAULT_MIN_BEFORE
    try:
        min_after = int(d.get("theme_min_after", str(DEFAULT_MIN_AFTER)))
    except Exception:
        min_after = DEFAULT_MIN_AFTER
    return deadline, min_before, min_after

def reserve_theme(theme_number: int, group_code: str) -> tuple[bool,str]:
    # aplica regra dinâmica
    deadline, min_before, min_after = current_rules()
    now = datetime.now()
    # obter id do grupo + contagem
    g = get_df("SELECT id FROM groups WHERE code=:c", {"c": group_code})
    if g.empty:
        return False, "Grupo não encontrado."
    gid = int(g.iloc[0]["id"])
    cnt = group_member_count(gid)
    min_req = min_before if now <= deadline else min_after
    if cnt < min_req:
        return False, f"Para reservar agora são necessários {min_req} membro(s) no grupo."
    # reservar
    with engine.begin() as conn:
        row = conn.execute(text("""
           UPDATE themes SET status='reservado', reserved_by=:gc, reserved_at=:ts
           WHERE number=:n AND status='livre'
        """), {"gc": group_code, "ts": now.strftime("%Y-%m-%d %H:%M:%S"), "n": theme_number})
        if row.rowcount == 0:
            return False, "Tema não está mais disponível."
    return True, "Tema reservado com sucesso."

def release_theme(theme_number: int, group_code: str) -> tuple[bool,str]:
    with engine.begin() as conn:
        row = conn.execute(text("""
            UPDATE themes SET status='livre', reserved_by=NULL, reserved_at=NULL
            WHERE number=:n AND reserved_by=:gc
        """), {"n": theme_number, "gc": group_code})
        if row.rowcount == 0:
            return False, "Não foi possível liberar (confira o tema e o grupo)."
    return True, "Tema liberado."

# =========================================================
# LOGIN
# =========================================================
def login_ui():
    st.title("Acesso")
    perfil = st.radio("Sou:", ["Aluno", "Docente"], horizontal=True)

    if perfil == "Aluno":
        ra   = st.text_input("RA do aluno")
        nome = st.text_input("Nome completo (como na lista)")
        if st.button("Entrar (Aluno)", type="primary"):
            if not ra or not nome:
                st.warning("Informe RA e nome.")
                return
            df = get_df("SELECT id,ra,name,turma FROM students WHERE ra=:ra AND name=:n AND active=1",
                        {"ra": ra.strip(), "n": nome.strip()})
            if df.empty:
                st.error("Aluno não encontrado. Verifique RA e nome (idêntico à lista).")
            else:
                st.session_state["auth"] = {
                    "role": "aluno",
                    "id":   int(df.iloc[0]["id"]),
                    "ra":   df.iloc[0]["ra"],
                    "name": df.iloc[0]["name"],
                    "turma":df.iloc[0]["turma"],
                }
                st.rerun()

    else:
        st.subheader("Login (Docente)")
        email = st.text_input("E-mail institucional")
        pin   = st.text_input("PIN", type="password")
        if st.button("Entrar (Docente)", type="primary"):
            email_norm = (email or "").strip().lower()
            with engine.begin() as conn:
                prof = conn.execute(text("""
                    SELECT id,name,email,role,pin,approved
                    FROM professors WHERE LOWER(email)=:e
                """), {"e": email_norm}).fetchone()
                # auto-provisiona admin se e-mail for ADMIN_EMAIL
                if not prof and email_norm == (ADMIN_EMAIL or "").lower():
                    conn.execute(text("""
                        INSERT INTO professors(name,email,role,pin,approved)
                        VALUES(:n,:e,'admin',:p,1)
                    """), {"n":"Administrador", "e":email_norm, "p": pin or ADMIN_PIN})
                    prof = conn.execute(text("""
                        SELECT id,name,email,role,pin,approved
                        FROM professors WHERE LOWER(email)=:e
                    """), {"e": email_norm}).fetchone()

            if not prof:
                st.error("Conta de docente não encontrada. Solicite acesso na Administração.")
            elif int(prof["approved"] or 0) != 1:
                st.warning("Conta pendente de aprovação.")
            elif (pin or "") != (prof["pin"] or ""):
                st.error("PIN inválido.")
            else:
                st.session_state["auth"] = {
                    "role": "admin" if prof["role"]=="admin" else "docente",
                    "id":   int(prof["id"]),
                    "name": prof["name"],
                    "email":prof["email"],
                }
                st.success("Login efetuado.")
                st.rerun()

if "auth" not in st.session_state:
    login_ui()
    st.stop()

auth = st.session_state["auth"]
st.sidebar.write(f"Logado como: **{auth.get('name','')}** ({auth.get('role','')})")
if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.rerun()

# =========================================================
# TABS POR PAPEL
# =========================================================
if auth["role"] == "aluno":
    tabs = st.tabs(["1) Grupos & Temas", "2) Upload"])
else:
    tabs = st.tabs(["1) Grupos & Temas", "2) Upload", "3) Galeria/Avaliação", "4) Administração", "5) Estudantes & Docentes"])

# =========================================================
# (1) GRUPOS & TEMAS
# =========================================================
with tabs[0]:
    st.header("Grupos & Temas")

    if auth["role"] == "aluno":
        my_grp = student_group(auth["ra"])
        if not my_grp:
            st.info("Você ainda não está em um grupo.")
            # criar grupo
            turmas = get_df("SELECT DISTINCT turma FROM students WHERE turma IS NOT NULL").sort_values("turma")["turma"].tolist()
            turma_escolhida = st.selectbox("Turma do grupo", turmas, index= turmas.index(auth.get("turma")) if auth.get("turma") in turmas else 0)
            if st.button("Criar grupo"):
                code = next_group_code_for_turma(turma_escolhida)
                now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            INSERT INTO groups(code,turma,created_by,created_at)
                            VALUES(:c,:t,:u,:ts)
                        """), {"c": code, "t": turma_escolhida, "u": auth["name"], "ts": now})
                        gid = conn.execute(text("SELECT id FROM groups WHERE code=:c"), {"c": code}).fetchone()[0]
                        conn.execute(text("INSERT INTO group_members(group_id,student_ra) VALUES(:g,:ra)"),
                                     {"g": gid, "ra": auth["ra"]})
                    st.success(f"Grupo criado: **{code}**")
                    st.rerun()
                except Exception as e:
                    st.error(f"Falha ao criar o grupo: {e}")
        else:
            st.success(f"Seu grupo: **{my_grp['code']}** (Turma {my_grp['turma']})")
            gid = int(my_grp["id"])

            # Lista de membros
            members = get_df("""
                SELECT s.ra, s.name, s.turma
                FROM students s JOIN group_members gm ON s.ra=gm.student_ra
                WHERE gm.group_id=:g ORDER BY s.name
            """, {"g": gid})
            st.write("**Membros:**")
            if members.empty:
                st.write("—")
            else:
                for _, r in members.iterrows():
                    st.write(f"- {r['name']} ({r['ra']}) – Turma {r['turma']}")

            st.markdown("---")
            st.subheader("Adicionar membro")
            unassigned = list_unassigned_students()
            turmas = ["(Todas)"] + sorted(unassigned["turma"].dropna().unique().tolist()) if not unassigned.empty else ["(Todas)"]
            tfilt = st.selectbox("Filtrar turma", turmas)
            view = unassigned if tfilt=="(Todas)" else unassigned[unassigned["turma"]==tfilt]
            query = st.text_input("Buscar por nome")
            if query:
                view = view[view["name"].str.contains(query, case=False, na=False)]
            if view.empty:
                st.info("Nenhum aluno disponível com esse filtro.")
            else:
                options = [""] + view.apply(lambda r: f"{r['name']} — {r['ra']} ({r['turma']})", axis=1).tolist()
                pick = st.selectbox("Selecionar aluno", options)
                if st.button("Adicionar ao grupo"):
                    if not pick:
                        st.warning("Selecione um aluno.")
                    else:
                        m = re.search(r"—\s([^\s]+)\s\(", pick)  # RA
                        if not m:
                            st.error("Não consegui identificar o RA.")
                        else:
                            sra = m.group(1)
                            try:
                                run_sql("INSERT INTO group_members(group_id,student_ra) VALUES(:g,:ra)", {"g": gid, "ra": sra})
                                st.success("Aluno adicionado.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erro ao adicionar: {e}")

            st.markdown("---")
            st.subheader("Temas")
            # status de reserva atual do grupo
            my_theme = get_df("SELECT number,title FROM themes WHERE reserved_by=:gc", {"gc": my_grp["code"]})
            if not my_theme.empty:
                st.info(f"Tema do grupo: **{int(my_theme.iloc[0]['number'])} – {my_theme.iloc[0]['title']}**")
                # liberar
                if st.button("Liberar tema"):
                    ok, msg = release_theme(int(my_theme.iloc[0]["number"]), my_grp["code"])
                    st.warning(msg) if ok else st.error(msg)
                    if ok: st.rerun()
            else:
                # mostrar regras
                deadline, min_before, min_after = current_rules()
                now = datetime.now()
                min_req = min_before if now <= deadline else min_after
                st.caption(f"Regra: mínimo **{min_req}** membro(s) para reservar. "
                           f"{'Disponível após ' + deadline.strftime('%d/%m/%Y %H:%M:%S') if now < deadline else 'Prazo já aberto.'}")

                cat = st.selectbox("Categoria", ["Todos","Privatização","Concessão","PPP","Financiamento/BNDES","Outro"])
                free = list_free_themes(cat)
                if free.empty:
                    st.info("Nenhum tema disponível.")
                else:
                    opts = [""] + free.apply(lambda r: f"{int(r['number']) if pd.notna(r['number']) else ''} – {r['title']}", axis=1).tolist()
                    pick = st.selectbox("Escolher tema", opts)
                    if st.button("Reservar tema"):
                        if not pick:
                            st.warning("Selecione um tema.")
                        else:
                            try:
                                num = int(pick.split("–")[0].strip())
                            except Exception:
                                st.error("Não consegui identificar o número do tema.")
                                num = None
                            if num is not None:
                                ok, msg = reserve_theme(num, my_grp["code"])
                                st.success(msg) if ok else st.error(msg)
                                if ok: st.rerun()

    else:
        # Docente/Admin: visão de todos os grupos
        groups = get_df("""
            SELECT g.code AS Grupo, g.turma AS Turma,
                   (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.id) AS Membros,
                   COALESCE((SELECT title FROM themes t WHERE t.reserved_by=g.code),'—') AS Tema
            FROM groups g
            ORDER BY g.turma, g.code
        """)
        st.dataframe(groups, use_container_width=True)

# =========================================================
# (2) UPLOAD
# =========================================================
with tabs[1]:
    st.header("Upload do Trabalho")
    if auth["role"] == "aluno":
        my_grp = student_group(auth["ra"])
        if not my_grp:
            st.error("Crie/entre em um grupo para enviar o trabalho.")
        else:
            code = my_grp["code"]
            st.write(f"Grupo: **{code}**")
            # tema (se houver)
            th = get_df("SELECT title FROM themes WHERE reserved_by=:gc", {"gc": code})
            st.write("Tema:", f"**{th.iloc[0]['title']}**" if not th.empty else "_(a reservar)_")

            # submissão atual
            cur = get_df("SELECT * FROM submissions WHERE group_code=:gc ORDER BY submitted_at DESC LIMIT 1", {"gc": code})
            if not cur.empty:
                row = cur.iloc[0]
                st.caption(f"Última submissão: {row['submitted_at']} por {row['submitted_by']}")
                # botões para baixar (se existirem)
                for label, col in [("📄 Relatório", "report_path"), ("🖥️ Slides", "slides_path"), ("🗜️ ZIP", "zip_path")]:
                    if row[col]:
                        fp = os.path.join(UPLOAD_DIR, row[col])
                        if os.path.exists(fp):
                            with open(fp, "rb") as f:
                                st.download_button(label, f, file_name=os.path.basename(fp), key=f"dl_{col}")
            st.markdown("---")
            # novo envio
            up_report = st.file_uploader("Relatório (PDF)", type=["pdf"])
            up_slides = st.file_uploader("Slides (PDF/PPT/PPTX)", type=["pdf","ppt","pptx"])
            up_zip    = st.file_uploader("Materiais (ZIP)", type=["zip"])
            video     = st.text_input("Link do vídeo (opcional)")
            consent   = st.checkbox("Cedo os direitos patrimoniais à PUC-SP para divulgação acadêmica/extensionista, com crédito aos autores.")
            if st.button("Enviar"):
                if not consent:
                    st.error("Marque a cessão de direitos para enviar.")
                else:
                    os.makedirs(UPLOAD_DIR, exist_ok=True)
                    def save(up, suffix):
                        if not up: return None
                        ext = os.path.splitext(up.name)[1]
                        name = f"{code}_{suffix}{ext if ext else ''}"
                        path = os.path.join(UPLOAD_DIR, name)
                        with open(path, "wb") as f:
                            f.write(up.getbuffer())
                        return name
                    rp = save(up_report, "relatorio")
                    sp = save(up_slides, "slides")
                    zp = save(up_zip, "extras")
                    theme_title = th.iloc[0]["title"] if not th.empty else None
                    run_sql("""
                        INSERT INTO submissions(group_code, theme_title, report_path, slides_path, zip_path, video_link,
                                                consent, submitted_by, submitted_at, approved)
                        VALUES(:g,:t,:r,:s,:z,:v,1,:u,:ts,0)
                    """, {"g": code, "t": theme_title, "r": rp, "s": sp, "z": zp, "v": video,
                          "u": auth["name"], "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    st.success("Submissão registrada.")
                    st.rerun()
    else:
        st.info("Esta seção é destinada aos alunos.")

# =========================================================
# (3) GALERIA / AVALIAÇÃO (Docentes)
# =========================================================
if auth["role"] in ("docente","admin") and len(tabs) >= 3:
    with tabs[2]:
        st.header("Galeria e Avaliação (Docentes)")
        subs = get_df("""
            SELECT id, group_code, theme_title, report_path, slides_path, zip_path, video_link
            FROM submissions WHERE approved=1 ORDER BY submitted_at DESC
        """)
        if subs.empty:
            st.info("Nenhuma submissão aprovada.")
        else:
            prof_id = auth["id"]
            for _, srow in subs.iterrows():
                st.markdown(f"### Grupo {srow['group_code']}")
                st.write("Tema:", srow["theme_title"] or "—")
                # Downloads
                for label, col in [("📄 Relatório", "report_path"), ("🖥️ Slides", "slides_path"), ("🗜️ ZIP", "zip_path")]:
                    if srow[col]:
                        fp = os.path.join(UPLOAD_DIR, srow[col])
                        if os.path.exists(fp):
                            with open(fp, "rb") as f:
                                st.download_button(label, f, file_name=os.path.basename(fp), key=f"{col}_{srow['id']}")
                if srow["video_link"]:
                    if any(h in srow["video_link"] for h in ["youtube.com","youtu.be"]):
                        st.video(srow["video_link"])
                    else:
                        st.write(f"[Vídeo]({srow['video_link']})")

                # Avaliação existente
                prev = get_df("""
                    SELECT score, liked FROM reviews
                    WHERE submission_id=:sid AND instructor_id=:iid
                """, {"sid": int(srow["id"]), "iid": int(prof_id)})
                prev_score = float(prev.iloc[0]["score"]) if not prev.empty and prev.iloc[0]["score"] is not None else 0.0
                prev_like  = bool(prev.iloc[0]["liked"]) if not prev.empty else False

                sc = st.slider("Nota (0–10)", 0.0, 10.0, prev_score, 0.5, key=f"score_{srow['id']}")
                lk = st.checkbox("Curtir", value=prev_like, key=f"like_{srow['id']}")
                if st.button("Salvar avaliação", key=f"save_{srow['id']}"):
                    run_sql("""
                        INSERT INTO reviews(submission_id,instructor_id,score,liked,created_at)
                        VALUES(:sid,:iid,:sc,:lk,:ts)
                        ON CONFLICT(submission_id, instructor_id) DO UPDATE
                        SET score=:sc, liked=:lk, created_at=:ts
                    """, {"sid": int(srow["id"]), "iid": int(prof_id), "sc": float(sc), "lk": 1 if lk else 0,
                          "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
                    st.success("Avaliação salva.")

# =========================================================
# (4) ADMINISTRAÇÃO (Admin)
# =========================================================
if auth["role"] == "admin" and len(tabs) >= 4:
    with tabs[3]:
        st.header("Administração")

        # Aprovação de submissões
        st.subheader("Aprovar Submissões")
        pend = get_df("SELECT id, group_code, submitted_at, submitted_by FROM submissions WHERE approved=0 ORDER BY submitted_at DESC")
        st.dataframe(pend, use_container_width=True)
        sel_ids = st.multiselect("IDs para aprovar", pend["id"].tolist() if not pend.empty else [])
        if st.button("Aprovar selecionadas"):
            for i in sel_ids:
                run_sql("UPDATE submissions SET approved=1 WHERE id=:i", {"i": int(i)})
            st.success("Aprovadas.")
            st.rerun()

        st.markdown("---")
        st.subheader("Temas (importar / liberar)")
        tdf = get_df("SELECT number,title,category,status,reserved_by,reserved_at FROM themes ORDER BY COALESCE(number,9999), title")
        st.dataframe(tdf, use_container_width=True)
        up = st.file_uploader("Importar JSON de temas", type=["json"])
        if up and st.button("Carregar temas"):
            tmp = os.path.join(DATA_DIR, "_themes_upload.json")
            with open(tmp, "wb") as f:
                f.write(up.read())
            addn = load_themes_from_json(tmp)
            st.success(f"Temas adicionados: {addn}")
            st.rerun()

        st.markdown("---")
        st.subheader("Regras de Reserva")
        cfg = {r["key"]: r["value"] for _, r in get_df("SELECT key,value FROM config").iterrows()}
        # deadline
        cur_deadline = cfg.get("theme_reserve_deadline", DEFAULT_DEADLINE)
        d, t = cur_deadline.split("T")
        new_d = st.date_input("Data-limite", value=datetime.strptime(d, "%Y-%m-%d").date())
        hh,mm,ss = map(int, t.split(":"))
        new_t = st.time_input("Hora-limite", value=datetime.strptime(t,"%H:%M:%S").time())
        # mínimos
        mb = st.number_input("Mínimo ANTES da data-limite", min_value=1, max_value=10, value=int(cfg.get("theme_min_before", DEFAULT_MIN_BEFORE)))
        ma = st.number_input("Mínimo DEPOIS da data-limite", min_value=1, max_value=10, value=int(cfg.get("theme_min_after", DEFAULT_MIN_AFTER)))
        if st.button("Salvar regras"):
            run_sql("UPDATE config SET value=:v WHERE key='theme_reserve_deadline'", {"v": f"{new_d}T{new_t}"})
            run_sql("UPDATE config SET value=:v WHERE key='theme_min_before'", {"v": str(int(mb))})
            run_sql("UPDATE config SET value=:v WHERE key='theme_min_after'", {"v": str(int(ma))})
            st.success("Regras atualizadas.")
            st.rerun()

        st.markdown("---")
        st.subheader("Gestão de Docentes")
        profs = get_df("SELECT id,name,email,role,pin,approved FROM professors ORDER BY role DESC, name")
        st.dataframe(profs, use_container_width=True)
        emails = st.multiselect("Aprovar/Desaprovar por e-mail", profs["email"].tolist() if not profs.empty else [])
        c1,c2 = st.columns(2)
        if c1.button("Aprovar selecionados"):
            for e in emails:
                run_sql("UPDATE professors SET approved=1 WHERE email=:e", {"e": e})
            st.success("Docentes aprovados.")
            st.rerun()
        if c2.button("Desaprovar selecionados"):
            for e in emails:
                run_sql("UPDATE professors SET approved=0 WHERE email=:e", {"e": e})
            st.info("Docentes desativados.")
            st.rerun()

        st.markdown("#### Criar/Editar Docente")
        c1,c2 = st.columns(2)
        name_e  = c1.text_input("Nome")
        email_e = c2.text_input("E-mail (único)")
        role_e  = c1.selectbox("Papel", ["docente","admin"])
        pin_e   = c2.text_input("PIN", type="password")
        approved_e = c1.checkbox("Aprovado", value=True)
        if st.button("Salvar docente"):
            if not email_e:
                st.error("Informe o e-mail.")
            else:
                run_sql("""
                    INSERT INTO professors (name,email,role,pin,approved)
                    VALUES(:n,:e,:r,:p,:a)
                    ON CONFLICT(email) DO UPDATE SET
                      name=:n, role=:r, pin=:p, approved=:a
                """, {"n": name_e.strip(), "e": email_e.strip().lower(),
                      "r": role_e, "p": pin_e, "a": 1 if approved_e else 0})
                st.success("Docente salvo/atualizado.")
                st.rerun()

        st.markdown("---")
        st.subheader("Importar Alunos")
        imp = st.file_uploader("CSV (colunas: RA, Nome, Turma) ou TXT PUC", type=["csv","txt"])
        if imp is not None:
            if imp.name.lower().endswith(".csv"):
                try:
                    dfc = pd.read_csv(imp)
                    added = 0
                    for _, r in dfc.iterrows():
                        ra = str(r.get("RA") or r.get("ra") or "").strip()
                        nm = str(r.get("Nome") or r.get("name") or "").strip()
                        tm = str(r.get("Turma") or r.get("turma") or "").strip()
                        if ra and nm:
                            run_sql("""
                                INSERT INTO students(ra,name,turma,active)
                                VALUES(:ra,:n,:t,1)
                                ON CONFLICT(ra) DO UPDATE SET name=:n, turma=:t, active=1
                            """, {"ra": ra, "n": nm, "t": tm})
                            added += 1
                    st.success(f"{added} aluno(s) importado(s).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro no CSV: {e}")
            else:
                # TXT PUC – parser opcional
                tmp = os.path.join(DATA_DIR, "_puc.txt")
                with open(tmp, "wb") as f:
                    f.write(imp.read())
                try:
                    from modules import import_txt
                    meta = import_txt.parse_puc_txt(tmp)
                    turma = meta.get("turma","")
                    students = meta.get("students",[])
                    added=0
                    for ra,nm in students:
                        run_sql("""
                            INSERT INTO students(ra,name,turma,active)
                            VALUES(:ra,:n,:t,1)
                            ON CONFLICT(ra) DO UPDATE SET name=:n, turma=:t, active=1
                        """, {"ra": ra.strip(), "n": nm.strip(), "t": turma})
                        added += 1
                    st.success(f"{added} aluno(s) importado(s) da turma {turma}.")
                    st.rerun()
                except Exception:
                    st.error("Parser TXT ausente/erro. Crie modules/import_txt.py com parse_puc_txt().")

# =========================================================
# (5) ESTUDANTES & DOCENTES (consulta)
# =========================================================
if auth["role"] in ("docente","admin") and len(tabs) >= 5:
    with tabs[4]:
        st.header("Estudantes & Docentes")
        st.subheader("Estudantes")
        q = st.text_input("Buscar (nome ou RA)")
        d = get_df("SELECT ra,name,turma FROM students WHERE active=1 ORDER BY turma,name")
        if q:
            d = d[d.apply(lambda r: q.lower() in str(r["ra"]).lower() or q.lower() in r["name"].lower(), axis=1)]
        st.dataframe(d, use_container_width=True)

        st.subheader("Docentes")
        p = get_df("SELECT name,email,role,approved FROM professors ORDER BY role DESC, name")
        if not p.empty:
            p = p.rename(columns={"name":"Nome","email":"E-mail","role":"Papel","approved":"Aprovado"})
        st.dataframe(p, use_container_width=True)

# Rodapé
st.caption("MVP – Submissões Industrial & EBC II (2º/2025)")
