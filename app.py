import os
from flask import Flask, render_template, request, redirect, url_for, session, g
from functools import wraps
import psycopg2
import psycopg2.extras

# --- App Initialization and Configuration ---
app = Flask(__name__)
app.config.from_mapping(
    SECRET_KEY='dev', # Change this to a random secret key in production
)

# --- Database Functions ---
def get_db():
    if 'db' not in g:
        # Get the database URL from environment variables (set by Render)
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            raise RuntimeError('DATABASE_URL is not set. Please set it in your environment.')
        g.db = psycopg2.connect(db_url)
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    with app.open_resource('schema.sql') as f:
        with db.cursor() as cur:
            cur.execute(f.read().decode('utf8'))
    db.commit()

@app.cli.command('init-db')
def init_db_command():
    """Clear the existing data and create new tables."""
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
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''
            SELECT 
                p.id, p.name, p.description, p.price, p.image_url, p.category, 
                STRING_AGG(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',') as available_flavors,
                COUNT(pf.flavor_id) as total_flavors_count
            FROM products p
            LEFT JOIN product_flavors pf ON p.id = pf.product_id
            LEFT JOIN flavors f ON pf.flavor_id = f.id
            GROUP BY p.id
            ORDER BY p.id DESC
        ''')
        products_raw = cur.fetchall()

    products = []
    for p in products_raw:
        product_dict = dict(p)
        product_dict['flavors'] = product_dict['available_flavors'].split(',') if product_dict['available_flavors'] else []
        product_dict['has_flavors'] = product_dict['total_flavors_count'] > 0
        products.append(product_dict)
    return render_template('index.html', products=products, category_title='Todos os Produtos')

@app.route('/category/<string:category_name>')
def show_category(category_name):
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('''
            SELECT 
                p.id, p.name, p.description, p.price, p.image_url, p.category, 
                STRING_AGG(CASE WHEN pf.stock > 0 THEN f.name ELSE NULL END, ',') as available_flavors,
                COUNT(pf.flavor_id) as total_flavors_count
            FROM products p
            LEFT JOIN product_flavors pf ON p.id = pf.product_id
            LEFT JOIN flavors f ON pf.flavor_id = f.id
            WHERE p.category = %s
            GROUP BY p.id
            ORDER BY p.id DESC
        ''', (category_name,))
        products_raw = cur.fetchall()

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
    selected_flavor = request.form.get('flavorSelection')

    if not product_id or not selected_flavor:
        return redirect(url_for('index'))

    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM products WHERE id = %s', (product_id,))
        product = cur.fetchone()

    if product:
        cart_item = {
            'id': product['id'],
            'name': product['name'],
            'price': product['price'],
            'flavor': selected_flavor
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
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM products ORDER BY id DESC')
        products_raw = cur.fetchall()
        cur.execute('SELECT * FROM flavors ORDER BY name')
        all_flavors = cur.fetchall()

    products = []
    for p in products_raw:
        product_dict = dict(p)
        with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur_flavors:
            cur_flavors.execute('SELECT flavor_id, stock FROM product_flavors WHERE product_id = %s', (p['id'],))
            associated_flavors_raw = cur_flavors.fetchall()
        product_dict['flavors_stock'] = {f['flavor_id']: f['stock'] for f in associated_flavors_raw}
        products.append(product_dict)

    return render_template('admin.html', products=products, all_flavors=all_flavors)

@app.route('/admin/add', methods=['POST'])
@login_required
def add_product():
    name = request.form['name']
    description = request.form['description']
    price = request.form['price']
    image_url = request.form['image_url']
    category = request.form['category']
    flavor_ids = request.form.getlist('flavors')

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            'INSERT INTO products (name, description, price, image_url, category) VALUES (%s, %s, %s, %s, %s) RETURNING id',
            (name, description, price, image_url, category)
        )
        product_id = cur.fetchone()[0]

        for flavor_id in flavor_ids:
            stock = request.form.get(f'stock_{flavor_id}', 0)
            cur.execute('INSERT INTO product_flavors (product_id, flavor_id, stock) VALUES (%s, %s, %s)', (product_id, int(flavor_id), stock))
    
    db.commit()
    return redirect(url_for('admin'))

@app.route('/admin/edit/<int:product_id>', methods=['POST'])
@login_required
def edit_product(product_id):
    name = request.form['name']
    description = request.form['description']
    price = request.form['price']
    image_url = request.form['image_url']
    category = request.form['category']
    flavor_ids = request.form.getlist('flavors')

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            'UPDATE products SET name = %s, description = %s, price = %s, image_url = %s, category = %s WHERE id = %s',
            (name, description, price, image_url, category, product_id)
        )
        cur.execute('DELETE FROM product_flavors WHERE product_id = %s', (product_id,))

        for flavor_id in flavor_ids:
            stock = request.form.get(f'stock_{flavor_id}', 0)
            cur.execute('INSERT INTO product_flavors (product_id, flavor_id, stock) VALUES (%s, %s, %s)', (product_id, int(flavor_id), stock))
    db.commit()
    return redirect(url_for('admin'))

@app.route('/admin/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('DELETE FROM products WHERE id = %s', (product_id,))
    db.commit()
    return redirect(url_for('admin'))

@app.route('/admin/flavors')
@login_required
def manage_flavors():
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute('SELECT * FROM flavors ORDER BY name')
        flavors = cur.fetchall()
    return render_template('admin_flavors.html', flavors=flavors)

@app.route('/admin/flavors/add', methods=['POST'])
@login_required
def add_flavor():
    name = request.form['name']
    if name:
        db = get_db()
        with db.cursor() as cur:
            cur.execute('INSERT INTO flavors (name) VALUES (%s) ON CONFLICT (name) DO NOTHING', (name,))
            db.commit()
    return redirect(url_for('manage_flavors'))

@app.route('/admin/flavors/delete/<int:flavor_id>', methods=['POST'])
@login_required
def delete_flavor(flavor_id):
    db = get_db()
    with db.cursor() as cur:
        cur.execute('DELETE FROM flavors WHERE id = %s', (flavor_id,))
    db.commit()
    return redirect(url_for('manage_flavors'))

# --- Main Execution ---
if __name__ == '__main__':
    app.run(debug=True)
