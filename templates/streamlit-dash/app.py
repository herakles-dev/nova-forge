import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Dashboard", layout="wide")

st.title("Dashboard")
st.markdown("Welcome to your Streamlit dashboard. Replace this content with your own.")

# Placeholder chart
df = pd.DataFrame({"Month": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"],
                   "Value": [10, 25, 18, 40, 32, 55]})

fig = px.line(df, x="Month", y="Value", title="Sample Trend",
              markers=True)
st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.caption("Built with Streamlit")
