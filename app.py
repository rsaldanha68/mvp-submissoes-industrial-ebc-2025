# Função para garantir que a tabela themes existe e importar de um JSON
def ensure_themes_from_json(path_json: str):
    """Carrega temas do JSON (campos: number,title,category) e faz merge sem duplicar títulos."""
    if not os.path.exists(path_json):
        return 0
    with open(path_json, "r", encoding="utf-8") as f:
        items = json.load(f)
    # garante colunas extra
    with engine.begin() as conn:
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS themes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER,
            title TEXT UNIQUE,
            category TEXT,
            status TEXT CHECK (status IN ('livre','reservado')) DEFAULT 'livre',
            reserved_by TEXT,
            reserved_at TEXT,
            released_by TEXT,
            released_at TEXT
        );""")
        existing = pd.read_sql("SELECT title FROM themes", conn)
        have = set(existing["title"].tolist()) if not existing.empty else set()
        inserted = 0
        for item in items:
            title = item["title"].strip()
            if title not in have:
                conn.execute(text("""INSERT INTO themes(number,title,category,status)
                                     VALUES(:n,:t,:c,'livre')"""),
                             {"n": int(item.get("number", 0) or 0),
                              "t": title,
                              "c": item.get("category", "Outro")})
                inserted += 1
        return inserted

# Carrega a lista padrão de 50 temas, se existir
_added = ensure_themes_from_json("data/themes_2025_2.json")
if _added:
    st.sidebar.success(f"Temas carregados: +{_added}")
