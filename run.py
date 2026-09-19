import os
from app import app, init_db

if __name__ == '__main__':
    init_db()
    app.run(host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '5000')), debug=False)
