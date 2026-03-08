#!/bin/bash
# Start the Rotaract Club Performance Tracker

cd "$(dirname "$0")"

# Create venv if needed
if [ ! -d "venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv venv
fi

# Activate and install deps
source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "🔵 Rotaract Club of Palghar — Performance Tracker"
echo "   Running at: http://localhost:8082"
echo "   Admin:  admin@rotaract-palghar.org / admin123"
echo "   Member: aarav@example.com / member123"
echo ""
python app.py
