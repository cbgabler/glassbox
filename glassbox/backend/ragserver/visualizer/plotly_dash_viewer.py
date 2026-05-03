import argparse
import json
from typing import Any, Dict, List, Optional
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError

import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go


def _post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlrequest.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_points(server_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _post_json(f"{server_url.rstrip('/')}/execute/export_vectors_plot", payload)


def _filter_points(points: List[Dict[str, Any]], source_filter: str) -> List[Dict[str, Any]]:
    if source_filter == "all":
        return points
    return [point for point in points if point.get("source") == source_filter]


def _build_figure(points: List[Dict[str, Any]], dims: int, source_filter: str) -> go.Figure:
    filtered = _filter_points(points, source_filter)
    finding_points = [point for point in filtered if point.get("source") == "finding"]
    code_points = [point for point in filtered if point.get("source") == "code"]

    if dims == 3:
        finding_trace = go.Scatter3d(
            x=[point["x"] for point in finding_points],
            y=[point["y"] for point in finding_points],
            z=[point.get("z", 0.0) for point in finding_points],
            mode="markers",
            name="Findings",
            marker={
                "size": 4,
                "color": "#66E8FF",
                "opacity": 0.9,
                "line": {"color": "#B8F5FF", "width": 1},
            },
            text=[json.dumps(point.get("metadata"), ensure_ascii=True) for point in finding_points],
            hoverinfo="text+name",
        )
        code_trace = go.Scatter3d(
            x=[point["x"] for point in code_points],
            y=[point["y"] for point in code_points],
            z=[point.get("z", 0.0) for point in code_points],
            mode="markers",
            name="Code",
            marker={
                "size": 4,
                "color": "#76FF03",
                "opacity": 0.92,
                "line": {"color": "#C8FF9E", "width": 1},
            },
            text=[json.dumps(point.get("metadata"), ensure_ascii=True) for point in code_points],
            hoverinfo="text+name",
        )
        fig = go.Figure(data=[finding_trace, code_trace])
        fig.update_layout(
            scene={
                "xaxis_title": "X",
                "yaxis_title": "Y",
                "zaxis_title": "Z",
            },
            margin={"l": 10, "r": 10, "t": 40, "b": 10},
            height=800,
            title="RAG Embedding Space (3D)",
        )
        return fig

    finding_trace = go.Scattergl(
        x=[point["x"] for point in finding_points],
        y=[point["y"] for point in finding_points],
        mode="markers",
        name="Findings",
        marker={"size": 6, "color": "#66E8FF", "opacity": 0.88, "line": {"color": "#B8F5FF", "width": 1}},
        text=[json.dumps(point.get("metadata"), ensure_ascii=True) for point in finding_points],
        hoverinfo="text+name",
    )
    code_trace = go.Scattergl(
        x=[point["x"] for point in code_points],
        y=[point["y"] for point in code_points],
        mode="markers",
        name="Code",
        marker={"size": 6, "color": "#76FF03", "opacity": 0.9, "line": {"color": "#C8FF9E", "width": 1}},
        text=[json.dumps(point.get("metadata"), ensure_ascii=True) for point in code_points],
        hoverinfo="text+name",
    )
    fig = go.Figure(data=[finding_trace, code_trace])
    fig.update_layout(
        xaxis_title="X",
        yaxis_title="Y",
        title="RAG Embedding Space (2D)",
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        height=800,
    )
    return fig


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dash viewer for RAG embeddings.")
    parser.add_argument("--run-id", required=True, help="RAG run id to visualize")
    parser.add_argument("--server-url", default="http://localhost:8000", help="RAG server base URL")
    parser.add_argument("--refresh-seconds", type=int, default=3, help="Refresh interval in seconds")
    parser.add_argument("--dims", type=int, default=3, choices=[2, 3], help="Number of reduced dimensions")
    parser.add_argument("--reduce-method", default="umap", choices=["umap", "pca", "none"], help="Reduction method")
    parser.add_argument("--max-points", type=int, default=100000, help="Max points to request")
    parser.add_argument("--max-findings", type=int, default=None, help="Max findings to request")
    parser.add_argument("--max-code", type=int, default=None, help="Max code chunks to request")
    parser.add_argument("--static-file", default=None, help="Optional JSON export file for offline viewing")
    parser.add_argument("--port", type=int, default=8050, help="Dash server port")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    static_payload: Optional[Dict[str, Any]] = None
    if args.static_file:
        with open(args.static_file, "r", encoding="utf-8") as handle:
            static_payload = json.load(handle)

    app = dash.Dash(__name__)
    app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            html, body, #react-entry-point {
                margin: 0;
                padding: 0;
                min-height: 100%;
                background: linear-gradient(180deg, #020802 0%, #041306 48%, #020a03 100%);
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""
    app.layout = html.Div(
        style={
            "maxWidth": "1240px",
            "margin": "0 auto",
            "padding": "24px",
            "minHeight": "100vh",
            "fontFamily": "Consolas, Menlo, monospace",
            "color": "#D6FFE8",
            "background": (
                "radial-gradient(circle at 20% 20%, rgba(27,94,32,0.30), transparent 40%),"
                "radial-gradient(circle at 80% 10%, rgba(118,255,3,0.20), transparent 35%),"
                "linear-gradient(180deg, #020802 0%, #041306 48%, #020a03 100%)"
            ),
        },
        children=[
            html.H2("RAG Embeddings Viewer", style={"letterSpacing": "1.2px", "textTransform": "uppercase"}),
            html.Div(
                style={"display": "flex", "gap": "16px", "alignItems": "center"},
                children=[
                    html.Label("Source", style={"fontWeight": "bold"}),
                    dcc.Dropdown(
                        id="source-filter",
                        options=[
                            {"label": "All", "value": "all"},
                            {"label": "Findings", "value": "finding"},
                            {"label": "Code", "value": "code"},
                        ],
                        value="all",
                        clearable=False,
                        style={"width": "220px", "color": "#091509"},
                    ),
                ],
            ),
            html.Div(
                style={
                    "marginTop": "12px",
                    "border": "1px solid rgba(118,255,3,0.38)",
                    "borderRadius": "12px",
                    "padding": "8px",
                    "boxShadow": "0 0 18px rgba(118,255,3,0.16), inset 0 0 20px rgba(118,255,3,0.08)",
                    "background": "rgba(2, 11, 4, 0.75)",
                },
                children=[dcc.Graph(id="embedding-graph")],
            ),
            dcc.Interval(id="refresh", interval=max(args.refresh_seconds, 1) * 1000, n_intervals=0),
        ],
    )

    @app.callback(
        Output("embedding-graph", "figure"),
        [Input("refresh", "n_intervals"), Input("source-filter", "value")],
    )
    def update_graph(_: int, source_filter: str) -> go.Figure:
        if static_payload is not None:
            payload = static_payload
        else:
            request_payload = {
                "run_id": args.run_id,
                "max_findings": args.max_findings,
                "max_code": args.max_code,
                "max_points": args.max_points,
                "reduce_method": args.reduce_method,
                "dims": args.dims,
            }
            try:
                payload = _fetch_points(args.server_url, request_payload)
            except (URLError, HTTPError):
                return go.Figure(layout={"title": "Waiting for server..."})

        points = payload.get("points", [])
        return _build_figure(points, args.dims, source_filter)

    app.run(debug=False, port=args.port)


if __name__ == "__main__":
    main()
