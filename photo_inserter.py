import pandas as pd
import psycopg2

# Database configuration
db_config = {
    'dbname': 'intoit-prod',
    'user': 'postgres',
    'password': 'Anshtheboss1',
    'host': 'intoit-prod.cx2s40qaqixr.us-east-1.rds.amazonaws.com',
}

# Read the CSV file
csv_path = 'places_with_links.csv'
data = pd.read_csv(csv_path)

# Connect to the PostgreSQL database
conn = psycopg2.connect(
    dbname=db_config['dbname'],
    user=db_config['user'],
    password=db_config['password'],
    host=db_config['host']
)
cursor = conn.cursor()

# Loop through each row in the CSV file
for index, row in data.iterrows():
    name = row['name']
    website_link = row['website_link'] if row['website_link'] != 'Not found' else None
    reservation_link = row['reservation_link'] if row['reservation_link'] != 'Not found' else None

    # Update the bars table
    update_query = """
    UPDATE bars
    SET website_link = %s, reservation_link = %s
    WHERE name = %s
    """
    cursor.execute(update_query, (website_link, reservation_link, name))
    print("updated for", name, website_link, reservation_link)

# Commit the changes and close the connection
conn.commit()
cursor.close()
conn.close()

print("Database update complete.")
