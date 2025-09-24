import os
from flask import Flask, render_template, request, redirect, url_for, session, g, json
from functools import wraps
from werkzeug.utils import secure_filename
import uuid
import stripe
from dotenv import load_dotenv

# --- App Initialization and Configuration ---
app = Flask(__name__)
app.config.from_mapping(
    SECRET_KEY='dev', # Change this to a random secret key in production
    UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads'),
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024 # Limite de 16MB para uploads
)

# Load .env file
load_dotenv()

# Stripe configuration
stripe.api_key = os.environ.get('STRIPE_SECRET_KEY')

def get_local_db_path():
    """Retorna o caminho para o arquivo do banco de dados local."""
    return os.path.join(app.instance_path, 'local_dev.sqlite')

def init_db():
    conn = get_db()
    try:
        with app.open_resource('schema.sql') as f:
            sql_script = f.read().decode('utf8')
            # Para SQLite, precisamos executar cada comando separadamente.
            # Para PostgreSQL, podemos executar o script inteiro.
            if not IS_PRODUCTION:
                # A conexão do SQLAlchemy Engine não tem um método `executescript`
                # então dividimos os comandos.
                for statement in sql_script.split(';'):
                    if statement.strip():
                        conn.execute(text(statement))
            else:
                # psycopg2 pode executar o script inteiro de uma vez
                conn.execute(sql_script)
        conn.commit()
    finally:
        close_db()

# --- Configuração para alternar entre banco de dados de produção e desenvolvimento ---
IS_PRODUCTION = os.environ.get('DATABASE_URL') is not None

# Garante que a pasta de uploads exista
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])


if not IS_PRODUCTION:
    print("AVISO: Variavel DATABASE_URL nao encontrada. Usando banco de dados SQLite local para desenvolvimento.")
    # Garante que a pasta 'instance' exista
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass # A pasta já existe
    
    # Configura o SQLAlchemy para o banco de dados local
    from flask_sqlalchemy import SQLAlchemy
    from sqlalchemy import text
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{get_local_db_path()}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db_local = SQLAlchemy(app)

    # --- Funções de DB para ambiente LOCAL (SQLite) ---
    def get_db():
        if 'db' not in g:
            g.db = db_local.engine.connect()
        return g.db

    @app.teardown_appcontext
    def close_db(e=None):
        db = g.pop('db', None)
        if db is not None:
            db.close()
    
    def execute_query(query, params=None, fetch=None):
        conn = get_db()
        # Adapta o placeholder de %s (psycopg2) para :param (SQLAlchemy)
        # e cria um dicionário de parâmetros.
        param_dict = {}
        if params:
            # Converte a query para o formato do SQLAlchemy (:p0, :p1, etc.)
            # e cria o dicionário de parâmetros correspondente.
            parts = query.split('%s')
            new_query_parts = [parts[0]]
            for i, part in enumerate(parts[1:]):
                param_name = f'p{i}'
                new_query_parts.append(f':{param_name}{part}')
                param_dict[param_name] = params[i]
            query = "".join(new_query_parts)
        
        result = conn.execute(text(query), param_dict)
        if fetch == 'all':
            return result.mappings().all()
        elif fetch == 'one':
            return result.mappings().one_or_none()
        else: # commit
            conn.commit()
            return result.lastrowid if result.rowcount > 0 else None

    def check_db_initialized():
        """Verifica se o arquivo do banco de dados local existe."""
        db_path = get_local_db_path()
        if not os.path.exists(db_path):
            return False
        # Verifica se o arquivo tem conteúdo, indicando que o schema foi criado.
        return os.path.getsize(db_path) > 0

    with app.app_context():
        if not check_db_initialized():
            print("Banco de dados local nao encontrado, criando um novo...")
            try:
                init_db() # Chama a função que executa o schema.sql
                print("Banco de dados local criado e inicializado com sucesso.")
            except Exception as e:
                print(f"ERRO ao inicializar o banco de dados local: {e}")
                print("Por favor, verifique o arquivo 'schema.sql' e as permissões da pasta.")
else:
    # Lógica de inicialização original para o Render (PostgreSQL)
    import psycopg2
    import psycopg2.extras

    def get_db():
        if 'db' not in g:
            db_url = os.environ.get('DATABASE_URL')
            g.db = psycopg2.connect(db_url)
        return g.db

    @app.teardown_appcontext
    def close_db(e=None):
        db = g.pop('db', None)
        if db is not None:
            db.close()
            
    def execute_query(query, params=None, fetch=None):
        conn = get_db()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(query, params)
            if fetch == 'all':
                return cur.fetchall()
            elif fetch == 'one':
                return cur.fetchone()
            elif fetch == 'id':
                # Usado para obter o ID retornado por RETURNING id
                res = cur.fetchone()
                return res[0] if res else None
            else: # commit
                conn.commit()
                # Se a query for um INSERT com RETURNING id, o ID estará no cursor
                if cur.description:
                    try:
                        return cur.fetchone()[0]
                    except (psycopg2.ProgrammingError, TypeError):
                        # Não havia nada para buscar (ex: UPDATE sem RETURNING)
                        return None

    def check_db_initialized():
        """Checks if the database has been initialized by checking for the products table."""
        conn = get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM products LIMIT 1;")
            return True
        except psycopg2.Error as e:
            if e.pgcode == '42P01': # "undefined_table"
                conn.rollback()
                return False
            raise
        finally:
            close_db()

    with app.app_context():
        if not check_db_initialized():
            print("Database tables not found on Render, initializing...")
            init_db()
            print("Database initialized on Render.")

@app.cli.command('init-db')
def init_db_command():
    """Limpa os dados existentes e cria novas tabelas."""
    init_db()
    print('Initialized the database.')

# --- Login Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

# Main store page
@app.route('/')
def index():
    # A função STRING_AGG do PostgreSQL não existe no SQLite. Usamos GROUP_CONCAT no SQLite.
    agg_function = "GROUP_CONCAT(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',')" if not IS_PRODUCTION else "STRING_AGG(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',')"
    
    query = f'''
        SELECT 
            p.id, p.name, p.description, p.price, p.image_url, p.category, 
            {agg_function} as available_flavors,
            COUNT(pf.flavor_id) as total_flavors_count
        FROM products p
        LEFT JOIN product_flavors pf ON p.id = pf.product_id
        LEFT JOIN flavors f ON pf.flavor_id = f.id
        GROUP BY p.id
        ORDER BY p.id DESC
    '''
    products_raw = execute_query(query, fetch='all')

    products = []
    for p in products_raw:
        product_dict = dict(p)
        product_dict['flavors'] = product_dict['available_flavors'].split(',') if product_dict['available_flavors'] else []
        product_dict['has_flavors'] = product_dict['total_flavors_count'] > 0
        products.append(product_dict)
    return render_template('index.html', products=products, category_title='Todos os Produtos')

@app.route('/category/<string:category_name>')
def show_category(category_name):
    agg_function = "GROUP_CONCAT(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',')" if not IS_PRODUCTION else "STRING_AGG(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',')"

    query = f'''
        SELECT 
            p.id, p.name, p.description, p.price, p.image_url, p.category, 
            {agg_function} as available_flavors,
            COUNT(pf.flavor_id) as total_flavors_count
        FROM products p
        LEFT JOIN product_flavors pf ON p.id = pf.product_id
        LEFT JOIN flavors f ON pf.flavor_id = f.id
        WHERE p.category = %s
        GROUP BY p.id
        ORDER BY p.id DESC
    '''
    products_raw = execute_query(query, (category_name,), fetch='all')

    products = []
    for p in products_raw:
        product_dict = dict(p)
        product_dict['flavors'] = product_dict['available_flavors'].split(',') if product_dict['available_flavors'] else []
        product_dict['has_flavors'] = product_dict['total_flavors_count'] > 0
        products.append(product_dict)
    return render_template('index.html', products=products, category_title=category_name)

# --- NEW: Shopping Cart Routes ---

@app.route('/add-to-cart', methods=['POST'])
def add_to_cart():
    if 'cart' not in session:
        session['cart'] = []

    product_id = request.form.get('product_id')
    flavor = request.form.get('flavor') # Get the selected flavor

    if not product_id:
        return redirect(url_for('index'))

    product = execute_query('SELECT * FROM products WHERE id = %s', (product_id,), fetch='one')

    if product:
        cart_item = {
            'id': product['id'],
            'name': product['name'],
            'price': product['price'],
            'flavor': flavor # Add flavor to the cart item
        }
        session['cart'].append(cart_item)
        session.modified = True

    return redirect(url_for('cart'))

@app.route('/cart')
def cart():
    cart_items = session.get('cart', [])
    total_price = sum(item['price'] for item in cart_items)
    return render_template('cart.html', cart_items=cart_items, total_price=total_price)

@app.route('/clear-cart')
def clear_cart():
    session.pop('cart', None)
    return redirect(url_for('index'))

# --- Admin and Login Routes ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form['username'] == 'admin' and request.form['password'] == 'password123':
            session['logged_in'] = True
            return redirect(url_for('admin'))
        else:
            error = 'Credenciais inválidas. Por favor, tente novamente.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin')
@login_required
def admin():
    products_raw = execute_query('SELECT * FROM products ORDER BY id DESC', fetch='all')
    all_sizes = execute_query('SELECT * FROM flavors ORDER BY name', fetch='all')

    products = []
    for p in products_raw:
        product_dict = dict(p) # Converte o objeto de banco de dados (imutável) para um dicionário (mutável)
        associated_flavors_raw = execute_query('SELECT flavor_id, stock FROM product_flavors WHERE product_id = %s', (p['id'],), fetch='all')
        product_dict['flavors_stock'] = {f['flavor_id']: f['stock'] for f in associated_flavors_raw}
        products.append(product_dict)
    
    return render_template('admin.html', products=products, all_flavors=all_sizes)

@app.route('/admin/add', methods=['POST'])
@login_required
def add_product():
    name = request.form['name']
    description = request.form['description']
    price = request.form['price']
    category = request.form['category']
    flavor_ids = request.form.getlist('flavors')
    image_url = None

    # Lógica de upload de imagem
    if 'image_file' in request.files:
        file = request.files['image_file']
        if file and file.filename != '':
            # Cria um nome de arquivo seguro e único
            filename = secure_filename(file.filename)
            unique_filename = str(uuid.uuid4()) + '_' + filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            # Salva a URL para ser acessada pelo navegador
            image_url = url_for('static', filename='uploads/' + unique_filename)

    # A função execute_query já retorna o ID do produto inserido, tanto para SQLite (lastrowid)
    # quanto para PostgreSQL (configurado para retornar o ID).
    insert_query = 'INSERT INTO products (name, description, price, image_url, category) VALUES (%s, %s, %s, %s, %s)'
    params = (name, description, price, image_url, category)
    
    if IS_PRODUCTION:
        insert_query += ' RETURNING id'

    product_id = execute_query(insert_query, params)
    
    for flavor_id in flavor_ids:
        stock = request.form.get(f'stock_{flavor_id}', 0)
        execute_query('INSERT INTO product_flavors (product_id, flavor_id, stock) VALUES (%s, %s, %s)', (product_id, int(flavor_id), stock))

    return redirect(url_for('admin'))

@app.route('/admin/edit/<int:product_id>', methods=['POST'])
@login_required
def edit_product(product_id):
    name = request.form['name']
    description = request.form['description']
    price = request.form['price']
    category = request.form['category']
    flavor_ids = request.form.getlist('flavors')
    
    # Mantém a imagem atual se nenhuma nova for enviada
    image_url = request.form.get('current_image_url')

    # Lógica de upload de nova imagem
    if 'image_file' in request.files:
        file = request.files['image_file']
        if file and file.filename != '':
            # (Opcional: aqui você poderia adicionar código para deletar a imagem antiga do servidor)
            filename = secure_filename(file.filename)
            unique_filename = str(uuid.uuid4()) + '_' + filename
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
            image_url = url_for('static', filename='uploads/' + unique_filename)

    execute_query(
        'UPDATE products SET name = %s, description = %s, price = %s, image_url = %s, category = %s WHERE id = %s',
        (name, description, price, image_url, category, product_id)
    )
    execute_query('DELETE FROM product_flavors WHERE product_id = %s', (product_id,))

    for flavor_id in flavor_ids:
        stock = request.form.get(f'stock_{flavor_id}', 0)
        execute_query('INSERT INTO product_flavors (product_id, flavor_id, stock) VALUES (%s, %s, %s)', (product_id, int(flavor_id), stock))

    return redirect(url_for('admin'))

@app.route('/admin/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    execute_query('DELETE FROM products WHERE id = %s', (product_id,))
    return redirect(url_for('admin'))

@app.route('/admin/flavors')
@login_required
def manage_flavors():
    flavors = execute_query('SELECT * FROM flavors ORDER BY name', fetch='all')
    return render_template('admin_flavors.html', flavors=flavors)

@app.route('/admin/flavors/add', methods=['POST'])
@login_required
def add_flavor():
    name = request.form['name']
    if name:
        # ON CONFLICT é do PostgreSQL. Para SQLite, usamos OR IGNORE.
        query = 'INSERT OR IGNORE INTO flavors (name) VALUES (%s)' if not IS_PRODUCTION else 'INSERT INTO flavors (name) VALUES (%s) ON CONFLICT (name) DO NOTHING'
        execute_query(query, (name,))
    return redirect(url_for('manage_flavors'))

@app.route('/admin/flavors/delete/<int:flavor_id>', methods=['POST'])
@login_required
def delete_flavor(flavor_id):
    execute_query('DELETE FROM flavors WHERE id = %s', (flavor_id,))
    return redirect(url_for('manage_flavors'))

# --- Stripe Payment Route ---
@app.route('/create-payment', methods=['POST'])
def create_payment():
    cart_items = session.get('cart', [])
    if not cart_items:
        return redirect(url_for('cart'))

    total_price = sum(item['price'] for item in cart_items)
    
    try:
        payment_intent = stripe.PaymentIntent.create(
            amount=int(total_price * 100),  # Amount in cents
            currency='brl',
            payment_method_types=['pix'],
        )
        return {
            'clientSecret': payment_intent.client_secret,
            'qrCodeData': payment_intent.next_action.pix_display_qr_code.data,
            'total': total_price
        }
    except Exception as e:
        return {'error': str(e)}, 403

# --- Main Execution ---
if __name__ == '__main__':
    app.run(debug=True)
