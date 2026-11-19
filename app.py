
import os
import uuid
import razorpay
import qrcode
import io
import base64
import psycopg2

from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.getenv("SECRET_KEY")

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)


# PostgreSQL connection wrapper
class DatabaseConnection:

    def __init__(self):
        self.connection = psycopg2.connect(
            os.getenv("DATABASE_URL"),
            cursor_factory=RealDictCursor
        )

    def execute(self, query, params=None):
        cursor = self.connection.cursor()
        query = query.replace("?", "%s")
        cursor.execute(query, params or ())
        return cursor

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def get_db():
    return DatabaseConnection()



# CREATE DATABASE TABLES
def init_db():

    conn = get_db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS foods (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                image TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                receipt_id TEXT UNIQUE NOT NULL,
                student_id INTEGER REFERENCES users(id),
                total_amount REAL NOT NULL,
                payment_status TEXT DEFAULT 'PENDING',
                order_status TEXT DEFAULT 'PENDING',
                delivery_status TEXT DEFAULT 'Not Delivered'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_items (
                id SERIAL PRIMARY KEY,
                receipt_id TEXT NOT NULL,
                food_name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                subtotal REAL NOT NULL
            )
        """)

        conn.commit()
        print("POSTGRESQL TABLES CREATED SUCCESSFULLY")

    except Exception as e:
        conn.rollback()
        print("DATABASE INITIALIZATION ERROR:", str(e))
        raise

    finally:
        conn.close()

# FOOD MENU
@app.route("/menu")
def menu():
    conn = get_db()
    foods = conn.execute("SELECT * FROM foods").fetchall()
    conn.close()

    return render_template("menu.html", foods=foods)


# ADD TO CART
@app.route("/add_to_cart/<int:food_id>", methods=["POST"])
def add_to_cart(food_id):
    conn = get_db()

    food = conn.execute(
        "SELECT * FROM foods WHERE id = ?", (food_id,)
    ).fetchone()

    conn.close()

    if food is None:
        flash("Food item not found!")
        return redirect(url_for("menu"))

    cart = session.get("cart", {})
    food_key = str(food_id)

    cart[food_key] = cart.get(food_key, 0) + 1

    session["cart"] = cart
    session.modified = True

    flash(food["name"] + " added to cart!")
    return redirect(url_for("menu"))


# CART SUMMARY
@app.route("/cart")
def cart():
    cart_items = session.get("cart", {})
    items = []
    total = 0

    if cart_items:
        conn = get_db()

        for food_id, quantity in cart_items.items():
            food = conn.execute(
                "SELECT * FROM foods WHERE id = ?",
                (food_id,)
            ).fetchone()

            if food:
                subtotal = food["price"] * quantity

                items.append({
                    "id": food["id"],
                    "name": food["name"],
                    "price": food["price"],
                    "quantity": quantity,
                    "subtotal": subtotal,
                    "image": food["image"]
                })

                total += subtotal

        conn.close()

    return render_template(
        "cart.html",
        items=items,
        total=total
    )


# UPDATE CART QUANTITY
@app.route("/update_cart/<int:food_id>/<action>", methods=["POST"])
def update_cart(food_id, action):
    cart_items = session.get("cart", {})
    food_key = str(food_id)

    if food_key in cart_items:
        if action == "increase":
            cart_items[food_key] += 1

        elif action == "decrease":
            cart_items[food_key] -= 1

            if cart_items[food_key] <= 0:
                cart_items.pop(food_key)

    session["cart"] = cart_items
    session.modified = True

    return redirect(url_for("cart"))


# REMOVE ITEM FROM CART
@app.route("/remove_from_cart/<int:food_id>", methods=["POST"])
def remove_from_cart(food_id):
    cart_items = session.get("cart", {})
    cart_items.pop(str(food_id), None)

    session["cart"] = cart_items
    session.modified = True

    return redirect(url_for("cart"))


# CREATE RAZORPAY PAYMENT ORDER
@app.route("/create_payment_order", methods=["POST"])
def create_payment_order():

    cart_items = session.get("cart", {})

    if not cart_items:
        flash("Your cart is empty!")
        return redirect(url_for("cart"))

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        flash("Razorpay configuration is missing!")
        return redirect(url_for("cart"))

    conn = get_db()
    total = 0

    for food_id, quantity in cart_items.items():

        food = conn.execute(
            "SELECT * FROM foods WHERE id = ?",
            (food_id,)
        ).fetchone()

        if food is None or quantity <= 0:
            conn.close()
            flash("Invalid cart item!")
            return redirect(url_for("cart"))

        total += food["price"] * quantity

    conn.close()

    amount_paise = int(round(total * 100))

    try:
        payment_order = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": "order_" + uuid.uuid4().hex[:10]
        })

        session["razorpay_order_id"] = payment_order["id"]
        session["payment_total"] = total

        return render_template(
            "payment.html",
            razorpay_key_id=RAZORPAY_KEY_ID,
            razorpay_order_id=payment_order["id"],
            amount=amount_paise,
            total=total
        )

    except Exception as e:
        print("Razorpay Error:", str(e))
        flash("Unable to create payment order. Please try again.")
        return redirect(url_for("cart"))


# VERIFY PAYMENT


@app.route("/verify_payment", methods=["POST"])
def verify_payment():

    if not session.get("user_id") or session.get("user_role") != "student":
        flash("Please login before placing an order!")
        return redirect(url_for("login"))

    payment_id = request.form.get("razorpay_payment_id")
    order_id = request.form.get("razorpay_order_id")
    signature = request.form.get("razorpay_signature")

    saved_order_id = session.get("razorpay_order_id")
    saved_total = session.get("payment_total")
    cart_items = session.get("cart", {})

    if not payment_id or not order_id or not signature:
        flash("Payment details are missing!")
        return redirect(url_for("cart"))

    if order_id != saved_order_id or not cart_items or saved_total is None:
        flash("Invalid or expired payment session!")
        return redirect(url_for("cart"))

    conn = get_db()

    try:
        total = 0
        bill_items = []

        for food_id, quantity in cart_items.items():

            food = conn.execute(
                "SELECT * FROM foods WHERE id = ?",
                (food_id,)
            ).fetchone()

            if food is None or quantity <= 0:
                raise ValueError("Invalid cart item!")

            price = float(food["price"])
            subtotal = price * quantity
            total += subtotal

            bill_items.append({
                "name": food["name"],
                "quantity": quantity,
                "price": price,
                "subtotal": subtotal
            })

        if round(total, 2) != round(float(saved_total), 2):
            raise ValueError("Cart amount has changed!")

        razorpay_client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        })

        payment_details = razorpay_client.payment.fetch(payment_id)

        if payment_details.get("order_id") != order_id:
            raise ValueError("Payment order mismatch!")

        if payment_details.get("status") != "captured":
            raise ValueError("Payment is not captured!")

        receipt_id = "CF" + uuid.uuid4().hex[:8].upper()
        student_id = session.get("user_id")

        conn.execute("""
            INSERT INTO orders
            (receipt_id, student_id, total_amount)
            VALUES (?, ?, ?)
        """, (receipt_id, student_id, total))

        for item in bill_items:
            conn.execute("""
                INSERT INTO order_items
                (receipt_id, food_name, quantity, unit_price, subtotal)
                VALUES (?, ?, ?, ?, ?)
            """, (
                receipt_id,
                item["name"],
                item["quantity"],
                item["price"],
                item["subtotal"]
            ))

        conn.commit()

        session.pop("cart", None)
        session.pop("razorpay_order_id", None)
        session.pop("payment_total", None)

        return redirect(url_for(
            "order_success",
            receipt_id=receipt_id
        ))

    except Exception as e:
        conn.rollback()
        print("Payment Verification Error:", str(e))
        flash("Payment verification failed. Please contact the canteen staff.")
        return redirect(url_for("cart"))

    finally:
        conn.close()


# STUDENT REGISTRATION

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        if not name or not email or not password:
            flash("Please fill all fields!")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters!")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)

        conn = get_db()

        try:
            conn.execute("""
                INSERT INTO users (name, email, password_hash, role)
                VALUES (?, ?, ?, 'student')
            """, (name, email, password_hash))

            conn.commit()

            print("STUDENT REGISTERED SUCCESSFULLY")

            flash("Registration successful! Please login.")
            return redirect(url_for("login"))

        except psycopg2.IntegrityError as e:
            conn.rollback()
            print("REGISTRATION DATABASE ERROR:", str(e))
            flash("Email already registered!")

        except Exception as e:
            conn.rollback()
            print("REGISTRATION ERROR:", str(e))
            flash("Registration failed. Check terminal.")

        finally:
            conn.close()

    return render_template("register.html")


# STUDENT LOGIN

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        conn = get_db()

        try:
            user = conn.execute("""
                SELECT * FROM users
                WHERE email = ? AND role = 'student'
            """, (email,)).fetchone()

        finally:
            conn.close()

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = "student"

            flash("Login successful!")
            return redirect(url_for("menu"))

        flash("Invalid email or password!")

    return render_template("login.html")


# INCHARGE LOGIN

@app.route("/incharge_login", methods=["GET", "POST"])
def incharge_login():

    if request.method == "POST":

        email = request.form.get("email")
        password = request.form.get("password")

        conn = get_db()

        try:
            user = conn.execute("""
                SELECT * FROM users
                WHERE email = ? AND role = 'incharge'
            """, (email,)).fetchone()

        finally:
            conn.close()

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = "incharge"

            flash("Incharge login successful!")
            return redirect(url_for("incharge_dashboard"))

        flash("Invalid incharge email or password!")

    return render_template("incharge_login.html")



# INCHARGE DASHBOARD

@app.route("/incharge_dashboard")
def incharge_dashboard():

    if session.get("user_role") != "incharge":
        flash("Please login as incharge!")
        return redirect(url_for("incharge_login"))

    conn = get_db()

    try:
        orders = conn.execute("""
            SELECT
                orders.receipt_id,
                orders.total_amount,
                orders.student_id,
                orders.delivery_status,
                users.name AS student_name,
                users.email AS student_email
            FROM orders
            LEFT JOIN users ON orders.student_id = users.id
            ORDER BY orders.id DESC
        """).fetchall()

    finally:
        conn.close()

    return render_template(
        "incharge_dashboard.html",
        orders=orders
    )

# STUDENT LOGOUT
@app.route("/logout")
def logout():

    session.clear()

    flash("Logged out successfully!")
    return redirect(url_for("home"))

# INCHARGE RECEIPT

@app.route("/incharge_receipt/<receipt_id>", methods=["GET", "POST"])
def incharge_receipt(receipt_id):

    if session.get("user_role") != "incharge":
        flash("Please login as incharge to view the bill!")
        return redirect(url_for("incharge_login"))

    conn = get_db()

    try:

        # Handle delivery status button clicks
        if request.method == "POST":

            new_status = request.form.get("delivery_status")

            if new_status not in ["Delivered", "Not Delivered"]:
                flash("Invalid delivery status!")
                return redirect(url_for(
                    "incharge_receipt",
                    receipt_id=receipt_id
                ))

            current_order = conn.execute("""
                SELECT delivery_status
                FROM orders
                WHERE receipt_id = ?
            """, (receipt_id,)).fetchone()

            if current_order is None:
                return "Receipt not found!", 404

            if current_order["delivery_status"] == "Delivered":
                flash("FOOD ALREADY DELIVERED!")

            else:
                conn.execute("""
                    UPDATE orders
                    SET delivery_status = ?
                    WHERE receipt_id = ?
                    AND COALESCE(delivery_status, 'Not Delivered') != 'Delivered'
                """, (new_status, receipt_id))

                conn.commit()

                if new_status == "Delivered":
                    flash("Food marked as DELIVERED successfully!")
                else:
                    flash("Order status: NOT DELIVERED")

            return redirect(url_for(
                "incharge_receipt",
                receipt_id=receipt_id
            ))

        # Get receipt and student details
        order = conn.execute("""
            SELECT
                orders.receipt_id,
                orders.total_amount,
                orders.delivery_status,
                users.name AS student_name,
                users.email AS student_email
            FROM orders
            LEFT JOIN users ON orders.student_id = users.id
            WHERE orders.receipt_id = ?
        """, (receipt_id,)).fetchone()

        if order is None:
            return "Receipt not found!", 404

        # Get food items
        items = conn.execute("""
            SELECT food_name, quantity, unit_price, subtotal
            FROM order_items
            WHERE receipt_id = ?
        """, (receipt_id,)).fetchall()

        return render_template(
            "incharge_receipt.html",
            order=order,
            items=items
        )

    finally:
        conn.close()

# ORDER SUCCESS

@app.route("/order_success/<receipt_id>")
def order_success(receipt_id):

    receipt_url = url_for(
        "incharge_receipt",
        receipt_id=receipt_id,
        _external=True
    )

    qr = qrcode.make(receipt_url)

    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")

    qr_base64 = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return render_template(
        "order_success.html",
        receipt_id=receipt_id,
        qr_code=qr_base64
    )


# INITIALIZE DATABASE
init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)