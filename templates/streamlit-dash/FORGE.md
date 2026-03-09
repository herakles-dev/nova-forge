# Streamlit Dashboard

This is a Streamlit data dashboard project.

## Stack
- Python 3.11
- Streamlit (UI framework)
- Pandas (data manipulation)
- Plotly (interactive charts)

## Running locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Running with Docker
```bash
docker build -t streamlit-dash .
docker run -p 8501:8501 streamlit-dash
```

## Customizing
Edit `app.py` to add your own data sources, charts, and widgets. Streamlit
re-runs the script top-to-bottom on every user interaction.
