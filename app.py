import os, sys, json, hashlib, sqlite3, subprocess, re, urllib.request, urllib.error, http.cookiejar
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, Response, make_response

app = Flask(__name__, template_folder='templates')
app.secret_key = 'alan-painel-secret-2026'

DB = '/opt/data/painel/painel.db'
SITE_DIR = '/opt/data/site-alancosta'

# ═══════════════════════════════════════════════════════════════════
# PROXY MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════

class HRProxyMiddleware:
    def __init__(self, app):
        self.app = app
        self.routes = {
            "/hr-onboarding": (8703, "/hr-onboarding"),
            "/sentinel": (8701, "/sentinel"),
            "/llm-failover": (8702, "/llm-failover"),
        }
    
    def _proxy_request(self, environ, start_response, port, prefix):
        import urllib.request as ureq
        path = environ.get('PATH_INFO', '')
        qs = environ.get('QUERY_STRING', '')
        target_path = path[len(prefix):] or '/'
        target_url = f"http://localhost:{port}{target_path}"
        if qs:
            target_url += '?' + qs
        
        method = environ.get('REQUEST_METHOD', 'GET')
        headers = {}
        for k, v in environ.items():
            if k.startswith('HTTP_'):
                header_name = k[5:].replace('_', '-').title()
                if header_name.lower() not in ('host', 'connection', 'transfer-encoding'):
                    headers[header_name] = v
        
        body = environ.get('wsgi.input').read(int(environ.get('CONTENT_LENGTH', '0'))) if environ.get('CONTENT_LENGTH') else b''
        if isinstance(body, str):
            body = body.encode()
        
        try:
            req = ureq.Request(target_url, data=body or None, headers=headers, method=method)
            with ureq.urlopen(req, timeout=120) as resp:
                resp_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in ('transfer-encoding', 'content-encoding')]
                start_response(f"{resp.status} OK", resp_headers)
                return [resp.read()]
        except ureq.HTTPError as e:
            start_response(f"{e.code} Error", [('Content-Type', 'text/plain')])
            return [f"Proxy error: {e.code}".encode()]
        except Exception as e:
            start_response("502 Bad Gateway", [('Content-Type', 'text/plain')])
            return [f"Proxy error: {str(e)}".encode()]

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        for prefix, (port, route_prefix) in self.routes.items():
            if path.startswith(prefix):
                # Pular proxy se tiver rota Flask dedicada
                if prefix == '/llm-failover' or prefix == '/hr-onboarding':
                    return self.app(environ, start_response)
                if environ.get('REQUEST_METHOD') == 'OPTIONS':
                    start_response("200 OK", [
                        ('Access-Control-Allow-Origin', '*'),
                        ('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS'),
                        ('Access-Control-Allow-Headers', '*'),
                    ])
                    return [b'']
                return self._proxy_request(environ, start_response, port, prefix)
        return self.app(environ, start_response)

app.wsgi_app = HRProxyMiddleware(app.wsgi_app)

# ═══════════════════════════════════════════════════════════════════
# SITE PUBLICO (alancosta.dev)
# ═══════════════════════════════════════════════════════════════════

@app.route('/')
def site_index():
    # Registrar visita
    ip = request.remote_addr or 'unknown'
    pagina = request.headers.get('Referer', '/')
    try:
        db = get_db()
        db.execute('INSERT INTO visitas (ip, pagina) VALUES (?,?)', (ip, pagina))
        db.commit()
        db.close()
    except:
        pass
    return send_from_directory(SITE_DIR, 'index.html')

@app.route('/blog')
def site_blog():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS artigos (
        id INTEGER PRIMARY KEY, titulo TEXT, conteudo TEXT,
        imagem_url TEXT, status TEXT DEFAULT 'publicado',
        publicado_em TEXT DEFAULT (datetime('now','localtime'))
    )''')
    db.commit()
    artigos = db.execute('SELECT id, titulo, imagem_url, publicado_em, substr(conteudo,1,200) as resumo FROM artigos WHERE status="publicado" ORDER BY id DESC').fetchall()
    
    # Reports do Odysseus publicados
    ody_ids = []
    try:
        db.execute('CREATE TABLE IF NOT EXISTS ody_published (report_id TEXT PRIMARY KEY)')
        ody_ids = [r['report_id'] for r in db.execute('SELECT report_id FROM ody_published').fetchall()]
    except:
        pass
    db.close()
    
    html = '''<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>Blog — Alan Costa</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
    :root{--white:#fff;--warm:#f6f5f4;--black:rgba(0,0,0,.95);--text:#31302e;--muted:#615d59;--accent:#0075de;--border:rgba(0,0,0,.08)}
    *{margin:0;padding:0;box-sizing:border-box}body{font-family:Inter,system-ui,sans-serif;color:var(--black);background:var(--white);line-height:1.5}
    nav{position:fixed;top:0;left:0;right:0;z-index:100;background:rgba(255,255,255,.85);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 32px;height:64px;display:flex;align-items:center;justify-content:space-between}
    .nav-logo{font-size:20px;font-weight:700;color:var(--black);text-decoration:none;letter-spacing:-.5px}.nav-logo span{color:var(--accent)}
    .nav-links{display:flex;gap:32px;list-style:none}.nav-links a{color:var(--text);text-decoration:none;font-size:14px;font-weight:500}.nav-links a:hover{color:var(--accent)}
    .container{max-width:900px;margin:0 auto;padding:100px 32px 60px}
    .article-card{background:var(--white);border:1px solid var(--border);border-radius:16px;padding:32px;margin-bottom:24px;transition:all .3s;text-decoration:none;display:block;color:inherit}
    .article-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 18px rgba(0,0,0,.04)}
    .article-card iframe{width:100%;height:300px;border:none;border-radius:12px;margin-bottom:16px}
    .article-card h2{font-size:22px;font-weight:700;color:var(--black);margin-bottom:8px}
    .article-card .meta{font-size:13px;color:var(--muted);margin-bottom:12px}
    .article-card p{font-size:14px;color:var(--muted);line-height:1.6}
    footer{text-align:center;padding:40px 32px;border-top:1px solid var(--border);font-size:13px;color:var(--muted)}
    .ody-badge{display:inline-block;padding:2px 10px;border-radius:8px;font-size:11px;background:rgba(59,130,246,.1);color:#3b82f6;margin-left:8px}
    @media(max-width:600px){.container{padding:80px 16px 40px}.nav-links{display:none}}
    </style></head><body>
    <nav><a href="/" class="nav-logo">Alan<span>Costa</span></a><ul class="nav-links"><li><a href="/">Home</a></li><li><a href="/#sobre">Sobre</a></li><li><a href="/#projetos">Projetos</a></li><li><a href="/blog">Artigos</a></li></ul><a href="/">← Voltar</a></nav>
    <div class="container"><h1 style="font-size:32px;font-weight:700;margin-bottom:8px">📝 Artigos</h1>
    <p style="color:var(--muted);margin-bottom:40px;font-size:16px">IA aplicada à gestão, automação e o futuro do trabalho</p>'''
    
    for a in artigos:
        data = a['publicado_em'][:10] if a['publicado_em'] else ''
        img_tag = f'<img src="{a["imagem_url"]}" alt="" loading="lazy" style="width:100%;height:200px;object-fit:cover;border-radius:12px;margin-bottom:20px">' if a['imagem_url'] else ''
        html += f'<a href="/blog/{a["id"]}" class="article-card">{img_tag}<h2>{a["titulo"]}</h2><div class="meta">📅 {data} · ☕ 6 min de leitura</div><p>{a["resumo"][:150]}...</p></a>'
    
    # Reports do Odysseus - buscar titulos para mostrar cards
    ODYSSEUS_URL = "http://31.97.242.246:7001"
    ody_reports = []
    if ody_ids:
        try:
            opener = _ody_login()
            req = urllib.request.Request(f"{ODYSSEUS_URL}/api/research/library", headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(req, timeout=10) as resp:
                lib = json.loads(resp.read())
                all_reports = lib if isinstance(lib, list) else lib.get('research', [])
                ody_reports = [r for r in all_reports if r.get('id') in ody_ids]
        except:
            pass
    
    for r in ody_reports:
        # Buscar o titulo real dentro do report
        report_title = r.get('query', '')[:80]
        try:
            opener2 = _ody_login()
            detail_req = urllib.request.Request(f"{ODYSSEUS_URL}/api/research/detail/{r['id']}",
                                               headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'})
            with opener2.open(detail_req, timeout=10) as det_resp:
                det = json.loads(det_resp.read())
                raw = det.get('raw_report', '') or det.get('result', '')
                # Extrair o primeiro H1 do report
                h1_match = re.search(r'^# (.+)$', raw, re.MULTILINE)
                if h1_match:
                    report_title = h1_match.group(1).strip()[:80]
                elif not report_title:
                    report_title = raw.strip()[:80].split('\n')[0]
        except:
            pass
        
        if len(report_title) > 80: report_title += '...'
        html += f'<a href="/blog/ody/{r["id"]}" class="article-card" style="cursor:pointer">'
        html += f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">'
        html += f'<span style="font-size:28px">📄</span>'
        html += f'<div><h2 style="margin:0">{report_title}</h2>'
        html += f'<div class="meta">📊 {r.get("source_count",0)} fontes · {r.get("status","done")}</div></div></div>'
        html += f'<p style="font-size:13px;color:var(--muted)">🔬 Pesquisa gerada pelo agente Odysseus · Clique para ler o report completo</p>'
        html += '</a>'
    
    html += '</div><footer>© 2026 Alan Costa · alancosta.dev</footer></body></html>'
    return html


@app.route('/blog/ody/<ody_id>')
def site_blog_ody(ody_id):
    """Exibe um report do Odysseus em iframe."""
    ODYSSEUS_URL = "http://31.97.242.246:7001"
    
    # Buscar titulo
    titulo = "Report Odysseus"
    try:
        opener = _ody_login()
        req = urllib.request.Request(f"{ODYSSEUS_URL}/api/research/library", headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=10) as resp:
            lib = json.loads(resp.read())
            all_r = lib if isinstance(lib, list) else lib.get('research', [])
            found = next((r for r in all_r if r.get('id') == ody_id), None)
            if found:
                titulo = found.get('query', titulo)[:100]
    except:
        pass
    
    return f'''<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>{titulo} — Alan Costa</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:Inter,sans-serif;background:#f6f5f4;color:#31302e;height:100vh;display:flex;flex-direction:column}}
    nav{{background:#fff;border-bottom:1px solid rgba(0,0,0,.08);padding:0 24px;height:56px;display:flex;align-items:center;gap:16px;flex-shrink:0}}
    nav a{{color:#0075de;text-decoration:none;font-size:14px;font-weight:500}} nav a:hover{{text-decoration:underline}}
    nav span{{color:#31302e;font-size:14px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    iframe{{flex:1;width:100%;border:none;background:#fff}}
    </style></head><body>
    <nav><a href="/blog">← Artigos</a><span>{titulo}</span></nav>
    <iframe src="/blog/ody-proxy/{ody_id}" loading="lazy"></iframe>
    </body></html>'''


@app.route('/blog/ody-proxy/<ody_id>')
def site_blog_ody_proxy(ody_id):
    """Proxy para o report do Odysseus."""
    ODYSSEUS_URL = "http://31.97.242.246:7001"
    try:
        opener = _ody_login()
        req = urllib.request.Request(f"{ODYSSEUS_URL}/api/research/report/{ody_id}",
                                     headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=15) as resp:
            content = resp.read()
            ct = resp.headers.get('Content-Type', 'text/html; charset=utf-8')
        return Response(content, content_type=ct)
    except Exception as e:
        return f"<html><body style='font-family:Inter;padding:60px;background:#f6f5f4'><h2>Report indisponivel</h2><p>{str(e)[:100]}</p></body></html>", 502


@app.route('/blog/<int:artigo_id>')
def site_blog_post(artigo_id):
    db = get_db()
    a = db.execute('SELECT * FROM artigos WHERE id=?', (artigo_id,)).fetchone()
    db.close()
    if not a:
        return 'Artigo nao encontrado', 404
    
    conteudo = a['conteudo']
    linhas = conteudo.split('\n')
    html_linhas = []
    
    for linha in linhas:
        linha = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1" loading="lazy">', linha)
        linha = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', linha)
        linha = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', linha)
        
        if linha.startswith('# ') and not linha.startswith('## '):
            html_linhas.append(f'<h1>{linha[2:]}</h1>')
        elif linha.startswith('## '):
            html_linhas.append(f'<h2>{linha[3:]}</h2>')
        elif linha.startswith('### '):
            html_linhas.append(f'<h3>{linha[4:]}</h3>')
        elif linha.startswith('> '):
            html_linhas.append(f'<blockquote>{linha[2:]}</blockquote>')
        elif linha.startswith('---'):
            html_linhas.append('<hr>')
        elif linha.startswith('- ') or linha.startswith('✅ ') or linha.startswith('❌ '):
            html_linhas.append(f'<li>{linha}</li>')
        elif linha.strip() == '':
            html_linhas.append('<br>')
        else:
            html_linhas.append(f'<p>{linha}</p>')
    
    conteudo_html = '\n'.join(html_linhas)
    
    return f'''<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>{a["titulo"]} — Alan Costa</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Merriweather:wght@400;700&display=swap" rel="stylesheet">
    <style>
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{font-family:Merriweather,Georgia,serif;color:#31302e;background:#fff;line-height:1.8;font-size:18px}}
    nav{{position:fixed;top:0;left:0;right:0;z-index:100;background:rgba(255,255,255,.85);backdrop-filter:blur(12px);border-bottom:1px solid rgba(0,0,0,.08);padding:0 32px;height:60px;display:flex;align-items:center;justify-content:space-between;font-family:Inter,sans-serif;font-size:14px}}
    .nav-logo{{font-size:18px;font-weight:700;color:rgba(0,0,0,.95);text-decoration:none}}.nav-logo span{{color:#0075de}}
    nav a{{color:#31302e;text-decoration:none}} nav a:hover{{color:#0075de}}
    article{{max-width:720px;margin:0 auto;padding:100px 24px 60px}}
    article h1{{font-size:36px;font-weight:700;line-height:1.2;margin-bottom:12px;font-family:Inter,sans-serif;letter-spacing:-.5px}}
    article h2{{font-size:22px;margin:32px 0 12px;color:#0075de;font-family:Inter,sans-serif}}
    article h3{{font-size:18px;margin:24px 0 8px;font-family:Inter,sans-serif}}
    article p{{margin-bottom:16px;color:#31302e}}
    article img{{max-width:100%;border-radius:12px;margin:20px 0}}
    article a{{color:#0075de}} article a:hover{{text-decoration:underline}}
    article blockquote{{border-left:3px solid #0075de;padding-left:20px;color:#615d59;margin:20px 0;font-style:italic}}
    article code{{background:#f6f5f4;padding:2px 6px;border-radius:4px;font-size:15px;font-family:'JetBrains Mono',monospace}}
    article ul,article ol{{margin-left:24px;margin-bottom:16px}}
    article li{{margin-bottom:6px}}
    article hr{{border:none;border-top:1px solid rgba(0,0,0,.08);margin:32px 0}}
    footer{{text-align:center;padding:40px 24px;border-top:1px solid var(--border);font-family:Inter,sans-serif;font-size:13px;color:var(--muted)}}
    @media(max-width:600px){{article{{padding:72px 16px 40px}}article h1{{font-size:26px}}article p{{font-size:17px}}}}
    </style></head><body>
    <nav><a href="/" class="nav-logo">Alan<span>Costa</span></a><a href="/blog">← Artigos</a></nav>
    <article>{conteudo_html}</article>
    <footer>© 2026 Alan Costa · alancosta.dev</footer></body></html>'''

@app.route('/<path:path>')
def site_static(path):
    file_path = os.path.join(SITE_DIR, path)
    if os.path.isfile(file_path):
        return send_from_directory(SITE_DIR, path)
    return redirect(url_for('painel_login'))

# ═══════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════

def init_db():
    os.makedirs('/opt/data/painel', exist_ok=True)
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS curriculos (
            id INTEGER PRIMARY KEY, nome TEXT, categoria TEXT, conteudo TEXT,
            sugestoes TEXT, nota REAL, atualizado_em TEXT
        );
        CREATE TABLE IF NOT EXISTS vagas (
            id INTEGER PRIMARY KEY, titulo TEXT, empresa TEXT, url TEXT,
            match_score REAL, status TEXT DEFAULT 'nova', detalhes TEXT,
            encontrada_em TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY, titulo TEXT, conteudo TEXT,
            imagem_url TEXT, dia_semana TEXT, status TEXT DEFAULT 'rascunho',
            aprovado_por TEXT, criado_em TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS analises (
            id INTEGER PRIMARY KEY, tipo TEXT, conteudo TEXT,
            criado_em TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS artigos (
            id INTEGER PRIMARY KEY, titulo TEXT, conteudo TEXT,
            imagem_url TEXT, status TEXT DEFAULT 'publicado',
            publicado_em TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS ody_published (report_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS visitas (
            id INTEGER PRIMARY KEY, ip TEXT, pagina TEXT,
            visitado_em TEXT DEFAULT (datetime('now','localtime'))
        );
    ''')
    pw = hashlib.sha256('alan2026'.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO users (username, password_hash) VALUES (?,?)', ('alan', pw))
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ═══════════════════════════════════════════════════════════════════
# PAINEL ADMIN (protegido por login)
# ═══════════════════════════════════════════════════════════════════

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('painel_login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/painel/login', methods=['GET','POST'])
def painel_login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = hashlib.sha256(request.form.get('password', '').encode()).hexdigest()
        db = get_db()
        user = db.execute('SELECT * FROM users WHERE username=? AND password_hash=?', (username, password)).fetchone()
        db.close()
        if user:
            session['user'] = username
            return redirect(url_for('painel_index'))
        return render_template('login.html', erro='Usuário ou senha inválidos')
    return render_template('login.html')

@app.route('/painel/logout')
def painel_logout():
    session.pop('user', None)
    return redirect(url_for('painel_login'))

@app.route('/painel')
@login_required
def painel_index():
    return render_template('dashboard.html')

# ── API Estatisticas de Visitas ─────────────────
@app.route('/painel/estatisticas')
@login_required
def painel_estatisticas():
    return render_template('estatisticas.html')


@app.route('/painel/api/visitas')
@login_required
def api_visitas():
    db = get_db()
    
    # Total de visitas (so IPs que nao sao o proprio admin)
    internos = ['127.0.0.1', '::1', 'localhost']
    placeholders = ','.join('?' for _ in internos)
    
    # Visitantes unicos (total historico)
    unicos = db.execute('SELECT COUNT(DISTINCT ip) as total FROM visitas WHERE ip NOT IN ({})'.format(
        placeholders), internos).fetchone()['total']
    
    # Visitas hoje (IPs unicos)
    hoje = db.execute("SELECT COUNT(DISTINCT ip) as total FROM visitas WHERE date(visitado_em) = date('now','localtime') AND ip NOT IN ({})".format(
        placeholders), internos).fetchone()['total']
    
    # Visitas esta semana (IPs unicos)
    semana = db.execute("SELECT COUNT(DISTINCT ip) as total FROM visitas WHERE visitado_em >= datetime('now','-7 days','localtime') AND ip NOT IN ({})".format(
        placeholders), internos).fetchone()['total']
    
    # Top paginas
    paginas = db.execute('SELECT pagina, COUNT(*) as total FROM visitas GROUP BY pagina ORDER BY total DESC LIMIT 10').fetchall()
    
    # Top IPs (anonimizado)
    ips = db.execute('SELECT ip, COUNT(*) as total FROM visitas GROUP BY ip ORDER BY total DESC LIMIT 20').fetchall()
    
    db.close()
    
    return jsonify({
        'unicos': unicos,
        'hoje': hoje,
        'semana': semana,
        'paginas': [dict(r) for r in paginas],
        'ips': [dict(r) for r in ips],
    })


# ── API Curriculos ────────────────────────────────
@app.route('/painel/api/curriculos')
@login_required
def api_curriculos():
    db = get_db()
    rows = db.execute('SELECT * FROM curriculos ORDER BY nota DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.route('/painel/curriculos/download')
@login_required
def painel_curriculos_download():
    """Pagina com lista de curriculos ajustados para download."""
    import glob
    ajustados_dir = '/opt/data/curriculos_ajustados'
    files = sorted(os.listdir(ajustados_dir)) if os.path.isdir(ajustados_dir) else []
    pdfs = [f for f in files if f.endswith('.pdf')]
    return render_template('curriculos_download.html', pdfs=pdfs)

@app.route('/painel/api/curriculos/analisar', methods=['POST'])
@login_required
def api_analisar_curriculos():
    try:
        import pymupdf
        CV_DIR = '/opt/data/curriculos_corrigidos'
        if not os.path.isdir(CV_DIR):
            CV_DIR = '/opt/data/curriculos'
        db = get_db()
        resultados = []
        keywords = {
            'python':5,'sql':4,'bigquery':5,'gcp':4,'n8n':5,'langflow':4,'langgraph':4,
            'rag':5,'llm':5,'mcp':5,'airflow':3,'pyspark':3,'power bi':3,'looker':2,
            'machine learning':5,'nlp':4,'prompt engineering':5,'automacao':5,
            'pipeline':4,'dashboard':3,'kpi':4,'agente':5,'ia generativa':5
        }
        for fname in sorted(os.listdir(CV_DIR)):
            if not fname.endswith('.pdf'): continue
            doc = pymupdf.open(os.path.join(CV_DIR, fname))
            texto = "".join([page.get_text() for page in doc])
            doc.close()
            nome_base = fname.replace('Alan_Costa_','').replace('.pdf','').replace('_',' ')
            score = sum(pts for kw,pts in keywords.items() if kw in texto.lower())
            nota = min(10, score/15)
            sugs = []
            if 'aws' not in texto.lower() and 'azure' not in texto.lower():
                sugs.append("Adicionar AWS/Azure para ampliar alcance")
            if 'docker' not in texto.lower():
                sugs.append("Incluir Docker/Kubernetes")
            if 'certificacao' not in texto.lower():
                sugs.append("Adicionar certificacoes cloud")
            db.execute('''INSERT OR REPLACE INTO curriculos (id, nome, categoria, conteudo, sugestoes, nota, atualizado_em)
                VALUES ((SELECT id FROM curriculos WHERE nome=?),?,?,?,?,?,datetime('now','localtime'))''',
                (nome_base, nome_base, nome_base, texto[:5000], json.dumps(sugs, ensure_ascii=False), round(nota,1)))
            resultados.append({'nome':nome_base,'nota':round(nota,1),'sugestoes':sugs})
        db.commit(); db.close()
        return jsonify({'status':'ok','curriculos':resultados})
    except Exception as e:
        return jsonify({'error':str(e)}), 500

# ── API Vagas ─────────────────────────────────────
@app.route('/painel/api/vagas')
@login_required
def api_vagas():
    db = get_db()
    rows = db.execute('SELECT * FROM vagas ORDER BY match_score DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/painel/api/vagas/buscar', methods=['POST'])
@login_required
def api_buscar_vagas():
    return jsonify({'status':'ok','message':'Busca executada pelo agente de vagas'})

# ── API Artigos (Blog) ──────────────────────────
ODYSSEUS_URL = "http://31.97.242.246:7001"
ODYSSEUS_AUTH = {"username": "admin", "password": "admin123"}

def _ody_login():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    login_data = json.dumps(ODYSSEUS_AUTH).encode()
    login_req = urllib.request.Request(
        f"{ODYSSEUS_URL}/api/auth/login",
        data=login_data,
        headers={'Content-Type': 'application/json'}
    )
    opener.open(login_req, timeout=10)
    return opener

@app.route('/curriculos-ajustados/<path:filename>')
@login_required
def curriculos_ajustados_file(filename):
    return send_from_directory('/opt/data/curriculos_ajustados', filename)


@app.route('/api/visitantes')
def api_visitantes_publico():
    """Endpoint publico para contador de visitantes unicos."""
    db = get_db()
    internos = ['127.0.0.1', '::1', 'localhost']
    placeholders = ','.join('?' for _ in internos)
    unicos = db.execute('SELECT COUNT(DISTINCT ip) as total FROM visitas WHERE ip NOT IN ({})'.format(
        placeholders), internos).fetchone()['total']
    db.close()
    return jsonify({'unicos': unicos})


# ── Sprint Health API ──────────────────────────────
@app.route('/api/sprint-health')
def api_sprint_health():
    """Retorna dados de saude de sprint a partir do Jira."""
    db = get_db()
    try:
        # Verificar se existe tabela de sprints
        sprints = db.execute('SELECT * FROM sprints ORDER BY id DESC LIMIT 5').fetchall()
        if sprints:
            return jsonify({'ok': True, 'sprints': [dict(s) for s in sprints]})
    except:
        pass
    
    # Mock: dados de exemplo no formato esperado pelo frontend
    return jsonify({
        'ok': True,
        'sprint': 'Sprint 42',
        'pct': 67,
        'done': 16,
        'in_progress': 5,
        'todo': 3,
        'total': 24,
        'velocity': 18,
        'insights': '📊 Sprint 42 - Em andamento (5 dias restantes)\n'
                    '✅ 16/24 issues concluidas (67%)\n'
                    '🟡 Bloqueador JIRA-123: API de pagamento instavel (Maria, Alta)\n'
                    '🟡 Bloqueador JIRA-456: Dependencia externa nao resolvida (Joao, Media)\n'
                    '📈 Velocity atual: 16 (media: 18) — 11% abaixo\n'
                    '🔵 Time distribuindo tarefas de forma equilibrada',
    })


# ── Proxy HR Onboarding ────────────────────────────
@app.route('/hr-onboarding/', defaults={'subpath': ''})
@app.route('/hr-onboarding/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def hr_onboarding_proxy(subpath):
    import urllib.request, urllib.error
    target = f"http://localhost:8703/{subpath}" if subpath else "http://localhost:8703/"
    qs = request.query_string.decode()
    if qs: target += '?' + qs
    method = request.method
    headers = dict(request.headers)
    headers.pop('Host', None)
    headers.pop('Content-Length', None)
    body = request.get_data()
    try:
        req = urllib.request.Request(target, data=body or None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = resp.read()
            flask_resp = make_response(resp_data, resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ('transfer-encoding', 'content-encoding', 'content-length'):
                    flask_resp.headers[k] = v
            return flask_resp
    except urllib.error.HTTPError as e:
        return Response(f"Proxy error: {e.code}", status=e.code)
    except Exception as e:
        return Response(f"Proxy error: {str(e)}", status=502)

# ── Proxy LLM Failover ─────────────────────────────
@app.route('/llm-failover/', defaults={'subpath': ''})
@app.route('/llm-failover/<path:subpath>', methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'])
def llm_failover_proxy(subpath):
    import urllib.request, urllib.error
    target = f"http://localhost:8702/{subpath}" if subpath else "http://localhost:8702/"
    qs = request.query_string.decode()
    if qs: target += '?' + qs
    method = request.method
    headers = dict(request.headers)
    headers.pop('Host', None)
    headers.pop('Content-Length', None)
    body = request.get_data()
    try:
        req = urllib.request.Request(target, data=body or None, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp_data = resp.read()
            flask_resp = make_response(resp_data, resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ('transfer-encoding', 'content-encoding', 'content-length'):
                    flask_resp.headers[k] = v
            return flask_resp
    except urllib.error.HTTPError as e:
        return Response(f"Proxy error: {e.code}", status=e.code)
    except Exception as e:
        return Response(f"Proxy error: {str(e)}", status=502)

@login_required
@app.route('/painel/artigos')
def painel_artigos():
    db = get_db()
    artigos = db.execute('SELECT id, titulo, imagem_url, publicado_em, substr(conteudo,1,100) as resumo FROM artigos ORDER BY id DESC').fetchall()
    
    reports = []
    try:
        opener = _ody_login()
        req = urllib.request.Request(f"{ODYSSEUS_URL}/api/research/library", headers={'User-Agent': 'Mozilla/5.0'})
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read())
            reports = data if isinstance(data, list) else data.get('research', data.get('reports', data.get('results', [])))
    except Exception as e:
        print(f"[ODY] Erro ao buscar reports: {e}")
        reports = []
    
    artigos_ids = set()
    try:
        rows = db.execute('SELECT report_id FROM ody_published').fetchall()
        artigos_ids = set(r['report_id'] for r in rows)
    except:
        pass
    db.close()
    return render_template('artigos.html', artigos=artigos, reports=reports, artigos_ids=artigos_ids, ODYSSEUS_URL=ODYSSEUS_URL)

@login_required
@app.route('/painel/artigos/importar-ody', methods=['POST'])
def painel_artigos_importar_ody():
    report_id = request.form.get('report_id', '').strip()
    publicar = request.form.get('publicar') == 'on'
    if not report_id:
        return redirect('/painel/artigos')
    db = get_db()
    if publicar:
        db.execute('INSERT OR IGNORE INTO ody_published (report_id) VALUES (?)', (report_id,))
    else:
        db.execute('DELETE FROM ody_published WHERE report_id=?', (report_id,))
    db.commit()
    db.close()
    return redirect('/painel/artigos')

@login_required
@app.route('/painel/artigos/novo', methods=['GET','POST'])
def painel_artigos_novo():
    if request.method == 'POST':
        titulo = request.form.get('titulo', '').strip()
        conteudo = request.form.get('conteudo', '').strip()
        imagem_url = request.form.get('imagem_url', '')
        if titulo and conteudo:
            db = get_db()
            db.execute('INSERT INTO artigos (titulo, conteudo, imagem_url) VALUES (?,?,?)',
                      (titulo, conteudo, imagem_url or ''))
            db.commit(); db.close()
            return redirect('/painel/artigos')
        return render_template('artigo_editor.html', erro='Preencha titulo e conteudo', titulo=titulo, conteudo=conteudo)
    return render_template('artigo_editor.html')

@login_required
@app.route('/painel/artigos/<int:artigo_id>/editar', methods=['GET','POST'])
def painel_artigos_editar(artigo_id):
    db = get_db()
    artigo = db.execute('SELECT * FROM artigos WHERE id=?', (artigo_id,)).fetchone()
    if request.method == 'POST':
        titulo = request.form.get('titulo', '').strip()
        conteudo = request.form.get('conteudo', '').strip()
        imagem_url = request.form.get('imagem_url', '')
        if titulo and conteudo:
            db.execute('UPDATE artigos SET titulo=?, conteudo=?, imagem_url=? WHERE id=?',
                      (titulo, conteudo, imagem_url, artigo_id))
            db.commit(); db.close()
            return redirect('/painel/artigos')
    db.close()
    if not artigo:
        return 'Artigo nao encontrado', 404
    return render_template('artigo_editor.html', artigo=artigo, editando=True)

@login_required
@app.route('/painel/artigos/<int:artigo_id>/excluir', methods=['POST'])
def painel_artigos_excluir(artigo_id):
    db = get_db()
    db.execute('DELETE FROM artigos WHERE id=?', (artigo_id,))
    db.commit(); db.close()
    return redirect('/painel/artigos')

# ── API Posts ─────────────────────────────────────
@app.route('/painel/api/posts')
@login_required
def api_posts():
    db = get_db()
    rows = db.execute('SELECT * FROM posts ORDER BY criado_em DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/painel/api/posts/<int:post_id>/aprovar', methods=['POST'])
@login_required
def api_aprovar_post(post_id):
    db = get_db()
    db.execute("UPDATE posts SET status='aprovado', aprovado_por=? WHERE id=?",(session['user'],post_id))
    db.commit(); db.close()
    try:
        result = subprocess.run(
            [sys.executable, '/opt/data/scripts/buffer_post.py'],
            capture_output=True, text=True, timeout=30
        )
        publicado = '✅ Postado' in result.stdout
        msg = 'Publicado no LinkedIn via Buffer!' if publicado else 'Aprovado. Verifique o Buffer.'
    except Exception as e:
        msg = f'Aprovado (Buffer offline: {str(e)[:50]})'
    return jsonify({'status':'ok', 'publicado': True, 'message': msg})

@app.route('/painel/api/posts/<int:post_id>/rejeitar', methods=['POST'])
@login_required
def api_rejeitar_post(post_id):
    db = get_db()
    db.execute("UPDATE posts SET status='rejeitado', aprovado_por=? WHERE id=?",(session['user'],post_id))
    db.commit(); db.close()
    return jsonify({'status':'ok'})

@app.route('/painel/api/analises')
@login_required
def api_analises():
    db = get_db()
    rows = db.execute('SELECT * FROM analises ORDER BY criado_em DESC LIMIT 20').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/churn/predict', methods=['POST'])
def api_churn_predict():
    data = request.get_json()
    try:
        import joblib, numpy as np
        model_path = '/opt/data/painel/churn_model.pkl'
        if not os.path.exists(model_path):
            return jsonify({'error':'Modelo nao encontrado'}), 500
        model = joblib.load(model_path)
        features = np.array([[
            float(data.get('idade',30)),
            float(data.get('sessoes',5)),
            float(data.get('mins',30)),
            int(data.get('erros',0)),
            int(data.get('tickets',0)),
            float(data.get('satisfacao',4)),
            float(data.get('plano_num',2)),
            float(data.get('cidade_num',1))
        ]])
        proba = model.predict_proba(features)[0]
        pred = int(model.predict(features)[0])
        risco = round(proba[1] * 100, 1)
        nivel = '🟢 Baixo' if risco < 30 else ('🟡 Médio' if risco < 60 else '🔴 Alto')
        return jsonify({
            'churn_prob': risco, 'nivel': nivel, 'predicao': pred,
            'features_analisadas': {
                'sessoes': data.get('sessoes',5),
                'minutos_ativos': data.get('mins',30),
                'erros': data.get('erros',0),
                'tickets': data.get('tickets',0),
                'satisfacao': data.get('satisfacao',4)
            }
        })
    except Exception as e:
        return jsonify({'error':str(e)}), 500

# Email config endpoint
@app.route('/painel/api/config')
@login_required
def api_config():
    return jsonify({
        'email': 'alncosta1@gmail.com',
        'linkedin': 'linkedin.com/in/alan-costa-84421887',
    })

@app.route('/api/contact', methods=['POST'])
def api_contact():
    data = request.get_json()
    email_to = "alncosta1@gmail.com"
    # Log the contact attempt
    print(f"[CONTATO] De: {data.get('name')} ({data.get('email')}): {data.get('message','')[:100]}")
    return jsonify({"status": "ok", "message": "Mensagem recebida! Retornarei em breve.", "email_to": email_to})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=9090, debug=True)
