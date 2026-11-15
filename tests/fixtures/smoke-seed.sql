-- Fictional articles for a disposable database. Never load into the archive.
CREATE TABLE websites (id INTEGER PRIMARY KEY, domain TEXT UNIQUE);
CREATE TABLE pages (
    id INTEGER PRIMARY KEY, website_id INTEGER REFERENCES websites(id),
    warc_record_id TEXT UNIQUE, url TEXT NOT NULL, warc_date TEXT
);
CREATE TABLE metadata (
    page_id INTEGER PRIMARY KEY REFERENCES pages(id),
    title TEXT, language TEXT, published_date TEXT
);
CREATE TABLE content (
    page_id INTEGER PRIMARY KEY REFERENCES pages(id), cleaned_text TEXT
);

INSERT INTO websites VALUES (1, 'en.example'), (2, 'ar.example'), (3, 'fr.example');
INSERT INTO pages VALUES
    (10, 1, 'dummy-en', 'https://en.example/ferry', '2025-01-07T09:00:00Z'),
    (11, 2, 'dummy-ar', 'https://ar.example/library', '2025-01-08T10:00:00Z'),
    (12, 3, 'dummy-fr', 'https://fr.example/museum', '2025-01-09T11:00:00Z');
INSERT INTO metadata VALUES
    (10, 'Alder Bay ferry service', 'en', '2025-01-06'),
    (11, 'مواعيد مكتبة السرو', 'ar', '2025-01-07'),
    (12, 'Ouverture du musée de Clairval', 'fr', '2025-01-08');
INSERT INTO content VALUES
    (10, 'The first ferry from Alder Bay leaves at 08:00 on Mondays.'),
    (11, 'تفتح مكتبة السرو أبوابها في الساعة العاشرة صباحًا أيام السبت.'),
    (12, 'Le musée de Clairval ouvre à neuf heures le dimanche.');
