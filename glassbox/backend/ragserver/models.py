from typing import Optional, List, Dict, Any, Literal
from enum import Enum
from pydantic import BaseModel, Field

class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

class Finding(BaseModel):
    id: str
    scanner: str               # sidechannel | secrets | git_history | endpoints | deps | hardware
    severity: Severity
    title: str
    description: str
    file: Optional[str] = None
    line: Optional[int] = None
    snippet: Optional[str] = None
    advice: str
    can_hw_confirm: bool = False

    def to_embed_text(self) -> str:
        """Produces the text string that gets embedded into FAISS."""
        text = f"[{self.severity}] [{self.scanner}] {self.title}\n{self.description}"
        if self.file:
            text += f"\nFile: {self.file}"
            if self.line:
                text += f":{self.line}"
        if self.snippet:
            text += f"\nCode: {self.snippet}"
        text += f"\nAdvice: {self.advice}"
        return text

class SearchRequest(BaseModel):
    run_id: str
    query: str
    k: int = 5
    severity_filter: Optional[Severity] = None
    scanner_filter: Optional[str] = None

class IndexCodeRequest(BaseModel):
    run_id: str
    repo_path: str

class AddFindingRequest(BaseModel):
    run_id: str
    finding: Finding

class ExportVectorsRequest(BaseModel):
    run_id: str
    max_findings: Optional[int] = None
    max_code: Optional[int] = None
    include_vectors: bool = True

class ExportVectorsPlotRequest(BaseModel):
    run_id: str
    max_findings: Optional[int] = None
    max_code: Optional[int] = None
    max_points: Optional[int] = None
    include_metadata: bool = True
    reduce_method: Literal["umap", "pca", "none"] = "umap"
    dims: int = 3
    random_seed: int = 42

class VectorPoint(BaseModel):
    x: float
    y: float
    z: Optional[float] = None
    source: Literal["finding", "code"]
    metadata: Optional[Dict[str, Any]] = None

class ExportVectorsPlotResponse(BaseModel):
    points: List[VectorPoint]
    dims: int
    reduce_method: str
    total_findings: int
    total_code: int
