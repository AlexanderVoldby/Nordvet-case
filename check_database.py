import pyodbc

print("Available ODBC drivers:", pyodbc.drivers())

connection_string = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    r"SERVER=localhost\SQLEXPRESS;"
    "DATABASE=Nordvet;"
    "Trusted_Connection=yes;"
    "Encrypt=yes;"
    "TrustServerCertificate=yes;"
)

with pyodbc.connect(connection_string, timeout=10) as connection:
    cursor = connection.cursor()
    cursor.execute("SELECT DB_NAME(), @@SERVERNAME;")
    database, server = cursor.fetchone()

    print(f"Connected to database: {database}")
    print(f"SQL Server: {server}")