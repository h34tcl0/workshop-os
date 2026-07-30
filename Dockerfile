FROM python:3.11-slim

WORKDIR /app

# Sin esto, Python "guarda" la salida en un buffer cuando no corre en una terminal
# interactiva (como dentro de Docker) — para procesos de larga duración como el
# daemon del scheduler, eso significa que `docker logs` puede mostrarse vacío
# indefinidamente aunque el programa funcione bien por dentro.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
