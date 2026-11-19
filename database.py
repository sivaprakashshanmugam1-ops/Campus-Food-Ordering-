
import sqlite3

def create_database():
    conn = sqlite3.connect("college_food.db")
    cursor = conn.cursor()

    # Students table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    # Foods table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS foods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT
        )
    """)

    # Orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id TEXT UNIQUE NOT NULL,
            student_id INTEGER,
            total_amount REAL NOT NULL,
            payment_status TEXT DEFAULT 'PENDING',
            order_status TEXT DEFAULT 'PENDING',
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)

    # Remove old spelling to avoid duplicate food
    cursor.execute(
        "DELETE FROM foods WHERE name = 'Chicken Biriyani'"
    )

    # Food items
    foods = [
        ("Chicken Rice", 120, "chicken_rice.jpg"),
        ("Chicken Briyani", 150, "chicken_briyani.jpg"),
        ("Egg Rice", 90, "egg_rice.jpg"),
        ("Veg Rice", 80, "veg_rice.jpg")
    ]

    # Insert food only if it does not already exist
    for name, price, image in foods:
        cursor.execute("""
            INSERT INTO foods (name, price, image)
            SELECT ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM foods WHERE name = ?
            )
        """, (name, price, image, name))

    # Update existing food details
    for name, price, image in foods:
        cursor.execute("""
            UPDATE foods
            SET price = ?, image = ?
            WHERE name = ?
        """, (price, image, name))

    conn.commit()
    conn.close()

    print("Database created successfully!")
    print("4 food items are ready!")


if __name__ == "__main__":
    create_database()
