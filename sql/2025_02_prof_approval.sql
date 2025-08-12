-- sql/2025_02_prof_approval.sql

-- adiciona coluna "approved" se ainda não existir
ALTER TABLE professors ADD COLUMN approved INTEGER DEFAULT 0;
ALTER TABLE professors ADD COLUMN created_at TEXT;

-- sementes (ajuste como quiser; podem ser apagadas depois)
INSERT OR IGNORE INTO professors (name,email,role,pin,approved,created_at)
VALUES
 ('Roland Veras Saldanha Junior','rsaldanha@pucsp.br','admin','admin',1,datetime('now')),
 ('JULIO MANUEL PIRES','jmpires@pucsp.br','docente','1234',1,datetime('now')),
 ('Tomas Bruginski de Paula','tbruginski@pucsp.br','docente','1234',1,datetime('now')),
 ('MARCIA FLAIRE PEDROZA','marciapedroza@pucsp.br','docente','1234',1,datetime('now')),
 ('JOAO MAMEDE CARDOSO','jmcardoso@pucsp.br','docente','1234',1,datetime('now'));
