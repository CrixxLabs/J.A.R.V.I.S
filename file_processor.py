"""
file_processor.py — Universal File Processor for JARVIS MARK VII

Safe file processing with support for:
- PDF, DOCX, TXT/Markdown, source code, JSON, CSV, XLSX
- Images, Audio, Video, Archives

Capabilities: inspect, summarize, explain, extract, OCR, transcribe,
code review, debug, data analysis, metadata inspection, safe conversion

Security: temporary files, path traversal protection, size limits
"""

import os
import json
import tempfile
import shutil
import subprocess
import mimetypes
import hashlib
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, BinaryIO
from dataclasses import dataclass, field
from datetime import datetime
import logging

from status_registry import EvidenceLevel, get_registry
from fractions import Fraction

from ocr_runtime import (
    OCREmptyResultError,
    OCRExecutionError,
    OCRTimeoutError,
    OCRUnavailableError,
    extract_image_text,
)

logger = logging.getLogger(__name__)

# Optional imports with graceful degradation
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False

try:
    import docx
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

try:
    import ffmpeg
    HAS_FFMPEG = True
except ImportError:
    HAS_FFMPEG = False

try:
    import magic
    HAS_MAGIC = True
except ImportError:
    HAS_MAGIC = False

# Constants
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
MAX_TOTAL_SIZE = 500 * 1024 * 1024  # 500MB total
ALLOWED_EXTENSIONS = {
    # Documents
    '.pdf', '.docx', '.txt', '.md', '.rtf',
    # Code
    '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.json', '.xml', '.yaml', '.yml',
    '.java', '.cpp', '.c', '.h', '.cs', '.go', '.rs', '.php', '.rb', '.swift', '.kt',
    '.sh', '.bat', '.ps1', '.sql', '.ini', '.cfg', '.toml', '.ini',
    # Data
    '.csv', '.tsv', '.xlsx', '.json', '.parquet',
    # Images
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.ico', '.svg',
    # Audio
    '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac',
    # Video
    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.wmv',
    # Archives
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar'
}

# Text-based extensions for direct reading
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css',
    '.json', '.xml', '.yaml', '.yml', '.java', '.cpp', '.c', '.h', '.cs',
    '.go', '.rs', '.php', '.rb', '.swift', '.kt', '.sh', '.bat', '.ps1',
    '.sql', '.ini', '.cfg', '.toml', '.yml', '.csv', '.tsv', '.json',
    '.xml', '.ini', '.cfg', '.conf', '.log'
}

# Binary extensions that need special handling
BINARY_EXTENSIONS = {
    '.pdf', '.docx', '.xlsx', '.png', '.jpg', '.jpeg',
    '.gif', '.bmp', '.webp', '.tiff', '.ico', '.mp3', '.wav', '.ogg',
    '.flac', '.m4a', '.aac', '.mp4', '.mov', '.avi', '.mkv', '.webm',
    '.wmv', '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar',
    '.doc', '.pptx', '.ppt', '.odt', '.ods', '.odp'
}

@dataclass
class FileMetadata:
    """File metadata container"""
    path: str
    name: str
    extension: str
    size: int
    mime_type: str
    sha256: str
    created: datetime
    modified: datetime
    is_text: bool
    is_binary: bool
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ProcessingResult:
    """Result of file processing operation"""
    success: bool
    action: str
    file_path: str
    result: Any = None
    error: Optional[str] = None
    metadata: Optional[FileMetadata] = None
    warnings: List[str] = field(default_factory=list)
    processing_time: float = 0.0

class FileProcessor:
    """
    Universal file processor with safety guarantees.
    
    Features:
    - Temporary file handling with automatic cleanup
    - Path traversal protection
    - Size limits enforcement
    - MIME type detection
    - Hash verification
    - Thread-safe operations
    """
    
    def __init__(
        self,
        max_file_size: int = MAX_FILE_SIZE,
        max_total_size: int = MAX_TOTAL_SIZE,
        temp_dir: Optional[str] = None,
        allowed_extensions: Optional[set] = None
    ):
        self.max_file_size = max_file_size
        self.max_total_size = max_total_size
        self.allowed_extensions = allowed_extensions or ALLOWED_EXTENSIONS
        self._temp_dir = temp_dir or tempfile.gettempdir()
        self._lock = threading.Lock()
        self._session_files: Dict[str, List[str]] = {}  # session_id -> temp file paths
        self._session_sizes: Dict[str, int] = {}  # session_id -> total bytes
        
        # Configure mimetypes
        mimetypes.init()
        # Add missing types
        extra_types = {
            '.md': 'text/markdown',
            '.py': 'text/x-python',
            '.js': 'application/javascript',
            '.ts': 'application/typescript',
            '.jsx': 'text/jsx',
            '.tsx': 'text/tsx',
            '.md': 'text/markdown',
            '.yaml': 'application/yaml',
            '.yml': 'application/yaml',
            '.toml': 'application/toml',
        }
        for ext, mime in extra_types.items():
            mimetypes.add_type(mime, ext)
    
    def _get_session_id(self) -> str:
        """Get current session identifier (thread-based for now)"""
        return f"session_{threading.current_thread().ident}"
    
    def _validate_path(self, file_path: str, session_id: str) -> Path:
        """Validate and resolve file path with path traversal protection"""
        path = Path(file_path).resolve()
        
        # Ensure path is within allowed directories
        allowed_dirs = [
            Path.home() / "Downloads",
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path(self._temp_dir),
            Path.cwd(),
        ]
        
        # Check if path is within allowed directories
        allowed = False
        for allowed_dir in allowed_dirs:
            try:
                path.relative_to(allowed_dir.resolve())
                allowed = True
                break
            except ValueError:
                continue
        
        if not allowed and not str(path).startswith(str(Path(self._temp_dir).resolve())):
            raise SecurityError(f"Access denied: path outside allowed directories: {file_path}")
        
        # Check file exists
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        if not path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")
        
        return path
    
    def _check_file_size(self, path: Path, session_id: str) -> None:
        """Check file size against limits"""
        size = path.stat().st_size
        if size > self.max_file_size:
            raise SizeLimitError(f"File size {size} exceeds limit {self.max_file_size}")
        
        current_total = self._session_sizes.get(session_id, 0)
        if current_total + size > self.max_total_size:
            raise SizeLimitError(f"Total session size would exceed limit {self.max_total_size}")
    
    def _check_extension(self, path: Path) -> None:
        """Check if file extension is allowed"""
        ext = path.suffix.lower()
        if ext not in self.allowed_extensions:
            raise UnsupportedFormatError(f"File extension not allowed: {ext}")
    
    def _compute_hash(self, path: Path, algorithm: str = 'sha256') -> str:
        """Compute file hash"""
        hasher = hashlib.new(algorithm)
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def _detect_mime(self, path: Path) -> str:
        """Detect MIME type using python-magic if available, else mimetypes"""
        if HAS_MAGIC:
            try:
                return magic.from_file(str(path), mime=True)
            except:
                pass
        mime, _ = mimetypes.guess_type(str(path))
        return mime or 'application/octet-stream'
    
    def extract_metadata(self, file_path: str, session_id: Optional[str] = None) -> FileMetadata:
        """Extract comprehensive file metadata"""
        session_id = session_id or self._get_session_id()
        path = self._validate_path(file_path, session_id)
        self._check_file_size(path, session_id)
        self._check_extension(path)
        
        stat = path.stat()
        mime = self._detect_mime(path)
        ext = path.suffix.lower()
        is_text = ext in TEXT_EXTENSIONS
        is_binary = ext in BINARY_EXTENSIONS or not is_text
        
        sha256 = self._compute_hash(path)
        
        metadata = FileMetadata(
            path=str(path),
            name=path.name,
            extension=ext,
            size=stat.st_size,
            mime_type=mime,
            sha256=sha256,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            is_text=is_text,
            is_binary=is_binary
        )
        
        # Add type-specific metadata
        try:
            if ext in {'.png', '.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff'} and HAS_PIL:
                with Image.open(path) as img:
                    metadata.extra['dimensions'] = img.size
                    metadata.extra['mode'] = img.mode
                    metadata.extra['format'] = img.format
            
            elif ext in {'.mp3', '.wav', '.ogg', '.flac', '.m4a'} and HAS_FFMPEG:
                try:
                    probe = ffmpeg.probe(str(path))
                    audio_info = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), {})
                    metadata.extra['duration'] = float(audio_info.get('duration', 0))
                    metadata.extra['bitrate'] = int(audio_info.get('bit_rate', 0))
                    metadata.extra['sample_rate'] = int(audio_info.get('sample_rate', 0))
                    metadata.extra['channels'] = int(audio_info.get('channels', 0))
                except:
                    pass
            
            elif ext in {'.mp4', '.mov', '.avi', '.mkv', '.webm'} and HAS_FFMPEG:
                try:
                    probe = ffmpeg.probe(str(path))
                    video_info = next((s for s in probe['streams'] if s['codec_type'] == 'video'), {})
                    metadata.extra['duration'] = float(video_info.get('duration', 0))
                    metadata.extra['width'] = int(video_info.get('width', 0))
                    metadata.extra['height'] = int(video_info.get('height', 0))
                    metadata.extra['fps'] = float(Fraction(str(video_info.get('r_frame_rate', '0/1'))))
                except:
                    pass
            
            elif ext == '.pdf' and HAS_PYPDF2:
                try:
                    with open(path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        metadata.extra['pages'] = len(reader.pages)
                        if reader.metadata:
                            metadata.extra['title'] = reader.metadata.get('/Title', '')
                            metadata.extra['author'] = reader.metadata.get('/Author', '')
                            metadata.extra['subject'] = reader.metadata.get('/Subject', '')
                            metadata.extra['creator'] = reader.metadata.get('/Creator', '')
                except:
                    pass
            
            elif ext in {'.docx', '.doc'} and HAS_DOCX:
                try:
                    doc = docx.Document(path)
                    metadata.extra['paragraphs'] = len(doc.paragraphs)
                    metadata.extra['tables'] = len(doc.tables)
                except:
                    pass
            
            elif ext in {'.xlsx', '.xls'} and HAS_OPENPYXL:
                wb = None
                try:
                    wb = openpyxl.load_workbook(path, read_only=True)
                    metadata.extra['sheets'] = wb.sheetnames
                    metadata.extra['sheet_count'] = len(wb.sheetnames)
                except:
                    pass
                finally:
                    if wb is not None:
                        wb.close()
                    
        except Exception as e:
            logger.warning(f"Failed to extract extended metadata for {path}: {e}")
        
        return metadata
    
    def read_text(self, file_path: str, session_id: Optional[str] = None, max_chars: int = 100000) -> str:
        """Read text content from file with size limit"""
        session_id = session_id or self._get_session_id()
        path = self._validate_path(file_path, session_id)
        self._check_file_size(path, session_id)
        self._check_extension(path)
        
        ext = path.suffix.lower()
        
        if ext == '.pdf' and HAS_PYPDF2:
            text_parts = []
            try:
                with open(path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            text_parts.append(text)
                        if sum(len(t) for t in text_parts) > max_chars:
                            break
                extracted = '\n\n'.join(text_parts)[:max_chars].strip()
                if not extracted:
                    raise NoExtractableTextError(
                        "PDF contains no extractable text; PDF OCR is not implemented"
                    )
                return extracted
            except Exception as e:
                if isinstance(e, NoExtractableTextError):
                    raise
                logger.warning(f"PDF text extraction failed: {e}")
                raise ExtractionError(f"PDF extraction failed ({type(e).__name__})") from e
        
        elif ext == '.docx' and HAS_DOCX:
            try:
                doc = docx.Document(path)
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                table_rows = []
                for table in doc.tables:
                    for row in table.rows:
                        values = [cell.text.strip() for cell in row.cells]
                        if any(values):
                            table_rows.append('\t'.join(values))
                return '\n'.join(paragraphs + table_rows)[:max_chars]
            except Exception as e:
                logger.warning(f"DOCX text extraction failed: {e}")
                raise ExtractionError(f"DOCX extraction failed ({type(e).__name__})") from e
        
        elif ext == '.xlsx' and HAS_OPENPYXL:
            wb = None
            try:
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                sheets_data = []
                total_chars = 0
                for sheet_name in wb.sheetnames:
                    ws = wb[sheet_name]
                    rows = []
                    for row in ws.iter_rows(values_only=True):
                        if any(v is not None for v in row):
                            rendered = '\t'.join(str(v) if v is not None else '' for v in row)
                            rows.append(rendered)
                            total_chars += len(rendered) + 1
                            if total_chars >= max_chars:
                                break
                    if rows:
                        sheets_data.append(f"--- {sheet_name} ---\n" + '\n'.join(rows))
                    if total_chars >= max_chars:
                        break
                return '\n\n'.join(sheets_data)[:max_chars]
            except Exception as e:
                logger.warning(f"Excel text extraction failed: {e}")
                raise ExtractionError(f"XLSX extraction failed ({type(e).__name__})") from e
            finally:
                if wb is not None:
                    wb.close()
        
        elif ext == '.pdf' and not HAS_PYPDF2:
            raise MissingDependencyError("PDF processing requires PyPDF2")
        
        elif ext == '.docx' and not HAS_DOCX:
            raise MissingDependencyError("DOCX processing requires python-docx")
        
        elif ext == '.xlsx' and not HAS_OPENPYXL:
            raise MissingDependencyError("XLSX processing requires openpyxl")
        
        elif ext in TEXT_EXTENSIONS:
            # Plain text files
            for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
                try:
                    with open(path, 'r', encoding=encoding) as f:
                        return f.read(max_chars)
                except UnicodeDecodeError:
                    continue
            raise ExtractionError("Unable to decode file as text")
        else:
            raise UnsupportedFormatError(f"Text extraction is not supported for {ext or 'this file type'}")
    
    def process_file(
        self,
        file_path: str,
        action: str,
        session_id: Optional[str] = None,
        **kwargs
    ) -> ProcessingResult:
        """
        Process file with specified action.
        
        Actions:
        - 'inspect': Extract metadata only
        - 'summarize': Generate summary of content
        - 'explain': Explain code/document content
        - 'extract': Extract text content
        - 'ocr': Extract text from images/PDFs via OCR
        - 'transcribe': Transcribe audio/video
        - 'convert': Convert to different format
        - 'analyze': Analyze data (CSV, JSON, etc.)
        - 'code_review': Review code for issues
        - 'debug': Debug code for errors
        - 'metadata': Extract detailed metadata
        """
        import time
        start_time = time.time()
        
        session_id = session_id or self._get_session_id()
        reserved_size = 0
        
        try:
            path = self._validate_path(file_path, session_id)
            self._check_file_size(path, session_id)
            self._check_extension(path)
            
            # Track session size
            size = path.stat().st_size
            with self._lock:
                self._session_sizes[session_id] = self._session_sizes.get(session_id, 0) + size
            reserved_size = size
            
            metadata = self.extract_metadata(str(path), session_id)
            
            # Route to appropriate handler
            result = None
            warnings = []
            
            action = action.lower()
            
            if action == 'inspect':
                result = self._action_inspect(path, metadata)
            
            elif action == 'summarize':
                result = self._action_summarize(path, metadata, **kwargs)
            
            elif action == 'explain':
                result = self._action_explain(path, metadata, **kwargs)
            
            elif action == 'extract':
                result = self._action_extract(path, metadata, **kwargs)
            
            elif action == 'ocr':
                result = self._action_ocr(path, metadata, **kwargs)
            
            elif action == 'transcribe':
                result = self._action_transcribe(path, metadata, **kwargs)
            
            elif action == 'convert':
                result = self._action_convert(path, metadata, **kwargs)
            
            elif action == 'analyze':
                result = self._action_analyze(path, metadata, **kwargs)
            
            elif action == 'code_review':
                result = self._action_code_review(path, metadata, **kwargs)
            
            elif action == 'debug':
                result = self._action_debug(path, metadata, **kwargs)
            
            elif action == 'metadata':
                result = metadata
            
            else:
                return ProcessingResult(
                    success=False,
                    action=action,
                    file_path=file_path,
                    error=f"Unknown action: {action}",
                    processing_time=time.time() - start_time
                )
            
            response = ProcessingResult(
                success=True,
                action=action,
                file_path=file_path,
                result=result,
                metadata=metadata,
                warnings=warnings,
                processing_time=time.time() - start_time
            )
            self._publish_processing_result(path.suffix.lower(), action, response)
            return response
            
        except Exception as e:
            logger.exception(f"File processing failed: {file_path}")
            response = ProcessingResult(
                success=False,
                action=action,
                file_path=file_path,
                error=str(e),
                processing_time=time.time() - start_time
            )
            self._publish_processing_result(Path(file_path).suffix.lower(), action, response, e)
            return response
        finally:
            if reserved_size:
                with self._lock:
                    remaining = max(0, self._session_sizes.get(session_id, 0) - reserved_size)
                    if remaining:
                        self._session_sizes[session_id] = remaining
                    else:
                        self._session_sizes.pop(session_id, None)
    
    # ── Action Implementations ─────────────────────────────────────────
    
    def _action_inspect(self, path: Path, metadata: FileMetadata) -> Dict[str, Any]:
        """Basic file inspection"""
        return {
            'file': metadata.name,
            'type': metadata.mime_type,
            'size': metadata.size,
            'size_human': self._format_size(metadata.size),
            'extension': metadata.extension,
            'sha256': metadata.sha256,
            'created': metadata.created.isoformat(),
            'modified': metadata.modified.isoformat(),
            'is_text': metadata.is_text,
            'extra': metadata.extra
        }
    
    def _action_summarize(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Generate summary of file content"""
        text = self.read_text(str(path))
        if not text or text.startswith('['):
            return f"Unable to summarize: {text}"
        
        # Truncate for summary
        max_len = kwargs.get('max_length', 500)
        if len(text) > max_len:
            return text[:max_len] + f"\n\n[... truncated, {len(text)} total characters]"
        return text
    
    def _action_explain(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Explain file content (code, document, etc.)"""
        text = self.read_text(str(path))
        if not text or text.startswith('['):
            return f"Unable to explain: {text}"
        
        ext = path.suffix.lower()
        
        if ext in {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.go', '.rs'}:
            return self._explain_code(text, metadata.extension)
        else:
            return self._explain_document(text, metadata.mime_type)
    
    def _explain_code(self, code: str, ext: str) -> str:
        """Generate explanation of code"""
        lines = code.split('\n')
        non_empty = [l for l in lines if l.strip()]
        
        # Simple static analysis
        imports = [l.strip() for l in lines if l.strip().startswith(('import ', 'from ', '#include', 'using ', 'require('))]
        functions = [l.strip() for l in lines if any(l.strip().startswith(kw) for kw in ['def ', 'function ', 'fn ', 'func ', 'public ', 'private ', 'protected '])]
        classes = [l.strip() for l in lines if l.strip().startswith(('class ', 'struct ', 'interface ', 'type '))]
        
        explanation = []
        explanation.append(f"Code Analysis ({ext}):")
        explanation.append(f"  Lines: {len(lines)} ({len(non_empty)} non-empty)")
        explanation.append(f"  Imports/Dependencies: {len(imports)}")
        explanation.append(f"  Functions/Methods: {len(functions)}")
        explanation.append(f"  Classes/Types: {len(classes)}")
        
        if imports:
            explanation.append(f"  Key imports: {', '.join(imports[:5])}{'...' if len(imports) > 5 else ''}")
        if functions:
            explanation.append(f"  Key functions: {', '.join([f.split('(')[0].split()[-1] for f in functions[:5]])}{'...' if len(functions) > 5 else ''}")
        if classes:
            explanation.append(f"  Classes/Types: {', '.join([c.split()[-1].rstrip('{:') for c in classes[:5]])}{'...' if len(classes) > 5 else ''}")
        
        return '\n'.join(explanation)
    
    def _explain_document(self, text: str, mime: str) -> str:
        """Explain document content"""
        words = len(text.split())
        lines = len(text.split('\n'))
        chars = len(text)
        
        # Simple keyword extraction
        words_lower = text.lower().split()
        freq = {}
        for w in words_lower:
            if len(w) > 4 and w.isalpha():
                freq[w] = freq.get(w, 0) + 1
        top_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:10]
        
        explanation = [
            f"Document Analysis:",
            f"  Characters: {chars:,}",
            f"  Words: {words:,}",
            f"  Lines: {lines:,}",
            f"  Type: {mime}",
            f"  Key terms: {', '.join(w for w, _ in top_words)}"
        ]
        return '\n'.join(explanation)
    
    def _action_extract(self, path: Path, metadata: FileMetadata, **kwargs) -> Dict[str, Any]:
        """Extract text/content from file"""
        text = self.read_text(str(path))
        return {
            'text': text,
            'length': len(text),
            'word_count': len(text.split()) if text else 0,
            'line_count': text.count('\n') + 1 if text else 0
        }
    
    def _action_ocr(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Bounded OCR extraction from image files only."""
        ext = path.suffix.lower()
        
        if ext in {'.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp'} and HAS_PIL:
            with Image.open(path) as source:
                image = source.convert('RGB')
                try:
                    from PIL import ImageEnhance
                    image = ImageEnhance.Contrast(image).enhance(2.0)
                    return extract_image_text(image, timeout=kwargs.get('timeout'))
                finally:
                    image.close()
        if ext == '.pdf':
            raise UnsupportedFormatError("PDF OCR is not implemented; only embedded PDF text extraction is supported")
        if not HAS_PIL:
            raise MissingDependencyError("Image OCR requires Pillow")
        raise UnsupportedFormatError(f"OCR is not supported for {ext or 'this file type'}")

    @staticmethod
    def _format_status(extension: str) -> tuple[str | None, str | None]:
        return {
            '.pdf': ('FILE_PDF_PARSER', 'FILE_PDF'),
            '.docx': ('FILE_DOCX_PARSER', 'FILE_DOCX'),
            '.xlsx': ('FILE_XLSX_PARSER', 'FILE_XLSX'),
        }.get(extension, ('FILE_TEXT_PARSER', 'FILE_TEXT') if extension in TEXT_EXTENSIONS else (None, None))

    def _publish_processing_result(self, extension: str, action: str,
                                   result: ProcessingResult, exc: Exception | None = None) -> None:
        registry = get_registry()
        if action == 'ocr':
            # ocr_runtime owns detailed Tesseract/OCR evidence; this layer owns
            # the file-processing claim around that operation.
            if result.success:
                detail = f"Image OCR extracted {len(str(result.result))} characters"
                registry.set_evidence("FILE_PROCESSOR", EvidenceLevel.LIVE, detail,
                                      source="file OCR operation")
                registry.set_evidence("FILE_IMAGE_PARSER", EvidenceLevel.LIVE,
                                      "Pillow opened and processed the source image",
                                      source="file OCR operation")
                registry.set_capability_evidence("FILE_IMAGE_OCR", EvidenceLevel.LIVE, detail,
                                                 source="file OCR operation")
            else:
                evidence = (EvidenceLevel.BLOCKED if isinstance(exc, (MissingDependencyError, OCRUnavailableError))
                            else EvidenceLevel.PROBED if isinstance(exc, (OCREmptyResultError, UnsupportedFormatError))
                            else EvidenceLevel.BROKEN)
                detail = result.error or "Image OCR failed"
                registry.set_evidence("FILE_PROCESSOR", evidence, detail,
                                      source="file OCR operation")
                registry.set_capability_evidence("FILE_IMAGE_OCR", evidence, detail,
                                                 source="file OCR operation")
            return
        subsystem, capability = self._format_status(extension)
        if result.success:
            detail = f"{extension or 'file'} {action} completed"
            registry.set_evidence("FILE_PROCESSOR", EvidenceLevel.LIVE, detail,
                                  source="file processing operation")
            extraction_actions = {'extract', 'summarize', 'explain', 'code_review', 'debug'}
            if subsystem and action in extraction_actions:
                registry.set_evidence(subsystem, EvidenceLevel.LIVE, detail,
                                      source="file extraction")
            if capability and action in extraction_actions:
                registry.set_capability_evidence(capability, EvidenceLevel.LIVE, detail,
                                                 source="file extraction")
            return
        if isinstance(exc, MissingDependencyError):
            evidence = EvidenceLevel.BLOCKED
        elif isinstance(exc, (ExtractionError, UnsupportedFormatError)):
            evidence = EvidenceLevel.PROBED
        else:
            evidence = EvidenceLevel.BROKEN
        detail = result.error or "File processing failed"
        registry.set_evidence("FILE_PROCESSOR", evidence, detail,
                              source="file processing operation")
        if subsystem:
            registry.set_evidence(subsystem, evidence, detail, source="file extraction")
        if capability:
            registry.set_capability_evidence(capability, evidence, detail,
                                             source="file extraction")
    
    def _action_transcribe(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Transcribe audio/video using Whisper"""
        if not HAS_WHISPER:
            return "[Transcription unavailable: whisper not installed]"
        
        try:
            model_name = kwargs.get('model', 'base')
            model = whisper.load_model(model_name)
            result = model.transcribe(str(path))
            return result['text']
        except Exception as e:
            return f"[Transcription failed: {e}]"
    
    def _action_convert(self, path: Path, metadata: FileMetadata, **kwargs) -> Dict[str, Any]:
        """Convert file to different format"""
        target_format = kwargs.get('format', '').lower()
        if not target_format:
            return {'error': 'Target format required'}
        
        ext = path.suffix.lower()
        output_path = path.with_suffix(f'.{target_format}')
        
        try:
            if ext in {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff'} and target_format in {'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tiff'} and HAS_PIL:
                with Image.open(path) as img:
                    if target_format in ('jpg', 'jpeg') and img.mode in ('RGBA', 'LA', 'P'):
                        img = img.convert('RGB')
                    img.save(output_path, format=target_format.upper())
                    return {'output': str(output_path), 'success': True}
            
            elif ext in {'.mp3', '.wav', '.ogg', '.flac', '.m4a'} and target_format in {'mp3', 'wav', 'ogg', 'flac'} and HAS_FFMPEG:
                try:
                    (ffmpeg.input(str(path)).output(str(output_path)).overwrite_output().run(capture_stdout=True, capture_stderr=True))
                    return {'output': str(output_path), 'success': True}
                except Exception as e:
                    return {'error': f'Audio conversion failed: {e}'}
            
            elif ext in {'.mp4', '.mov', '.avi', '.mkv', '.webm'} and target_format in {'mp4', 'webm', 'mov'} and HAS_FFMPEG:
                try:
                    (ffmpeg.input(str(path)).output(str(output_path)).overwrite_output().run(capture_stdout=True, capture_stderr=True))
                    return {'output': str(output_path), 'success': True}
                except Exception as e:
                    return {'error': f'Video conversion failed: {e}'}
            
            return {'error': f'Conversion from {ext} to {target_format} not supported'}
        except Exception as e:
            return {'error': f'Conversion failed: {e}'}
    
    def _action_analyze(self, path: Path, metadata: FileMetadata, **kwargs) -> Dict[str, Any]:
        """Analyze data files (CSV, JSON, etc.)"""
        ext = path.suffix.lower()
        
        if ext in {'.csv', '.tsv'}:
            try:
                import pandas as pd
                df = pd.read_csv(path, sep=',' if ext == '.csv' else '\t')
                return {
                    'shape': df.shape,
                    'columns': list(df.columns),
                    'dtypes': {c: str(t) for c, t in df.dtypes.items()},
                    'memory_mb': df.memory_usage(deep=True).sum() / 1024 / 1024,
                    'null_counts': df.isnull().sum().to_dict(),
                    'sample': df.head(5).to_dict('records'),
                    'stats': df.describe(include='all').to_dict()
                }
            except ImportError:
                return {'error': 'pandas required for CSV analysis'}
            except Exception as e:
                return {'error': f'CSV analysis failed: {e}'}
        
        elif ext == '.json':
            try:
                with open(path) as f:
                    data = json.load(f)
                return {
                    'type': type(data).__name__,
                    'keys': list(data.keys()) if isinstance(data, dict) else None,
                    'length': len(data) if isinstance(data, (list, dict)) else None,
                    'sample': str(data)[:500]
                }
            except Exception as e:
                return {'error': f'JSON analysis failed: {e}'}
        
        return {'error': f'Analysis not supported for {ext}'}
    
    def _action_code_review(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Review code for issues"""
        text = self.read_text(str(path))
        if not text or text.startswith('['):
            return f"Unable to review: {text}"
        
        issues = []
        lines = text.split('\n')
        
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # Check for common issues
            if 'TODO' in stripped or 'FIXME' in stripped or 'HACK' in stripped:
                issues.append(f"Line {i}: {stripped[:80]}")
            
            if len(line) > 120:
                issues.append(f"Line {i}: Line too long ({len(line)} chars)")
            
            if 'eval(' in stripped or 'exec(' in stripped:
                issues.append(f"Line {i}: Dangerous eval/exec usage")
            
            if 'password' in stripped.lower() and ('=' in stripped or ':' in stripped):
                issues.append(f"Line {i}: Possible hardcoded password")
            
            if 'secret' in stripped.lower() and ('=' in stripped or ':' in stripped):
                issues.append(f"Line {i}: Possible hardcoded secret")
            
            # Check for SQL injection patterns
            if any(p in stripped.lower() for p in ['select *', 'union select', 'drop table', 'insert into', 'delete from']):
                if '?' not in stripped and '%s' not in stripped and '{' not in stripped:
                    issues.append(f"Line {i}: Possible SQL injection vulnerability")
        
        if not issues:
            return "Code review passed - no obvious issues found."
        
        return f"Code Review Findings ({len(issues)} issues):\n" + '\n'.join(f"  - {issue}" for issue in issues[:20])
    
    def _action_debug(self, path: Path, metadata: FileMetadata, **kwargs) -> str:
        """Debug code for errors"""
        text = self.read_text(str(path))
        if not text or text.startswith('['):
            return f"Unable to debug: {text}"
        
        ext = path.suffix.lower()
        debug_info = []
        
        if ext == '.py':
            # Try to compile and catch syntax errors
            try:
                compile(text, str(path), 'exec')
                debug_info.append("Syntax: OK")
            except SyntaxError as e:
                debug_info.append(f"Syntax Error at line {e.lineno}: {e.msg}")
            
            # Check for common runtime issues
            lines = text.split('\n')
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if 'except:' == stripped or 'except :' == stripped:
                    debug_info.append(f"Line {i}: Bare except clause")
                if 'print(' in stripped and 'debug' not in stripped.lower():
                    debug_info.append(f"Line {i}: Debug print statement")
        
        elif ext in {'.js', '.ts', '.jsx', '.tsx'}:
            debug_info.append("JavaScript/TypeScript: Consider running ESLint or TypeScript compiler for full analysis")
        
        return '\n'.join(debug_info) if debug_info else "No obvious issues detected."
    
    def _format_size(self, size: int) -> str:
        """Format file size human-readable"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
    
    # ── Session Management ─────────────────────────────────────────────
    
    def register_session(self, session_id: str) -> None:
        """Register a new processing session"""
        with self._lock:
            self._session_files[session_id] = []
            self._session_sizes[session_id] = 0
    
    def cleanup_session(self, session_id: str) -> int:
        """Clean up temporary files for a session"""
        cleaned = 0
        with self._lock:
            files = self._session_files.pop(session_id, [])
            for f in files:
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        cleaned += 1
                except Exception as e:
                    logger.warning(f"Failed to clean up {f}: {e}")
            self._session_sizes.pop(session_id, None)
        return cleaned
    
    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """Get statistics for a session"""
        with self._lock:
            files = self._session_files.get(session_id, [])
            total_size = self._session_sizes.get(session_id, 0)
            return {
                'file_count': len(files),
                'total_size': total_size,
                'total_size_human': self._format_size(total_size)
            }


# ── Custom Exceptions ─────────────────────────────────────────────────

class FileProcessorError(Exception):
    """Base exception for file processor errors"""
    pass

class SecurityError(FileProcessorError):
    """Path traversal or access violation"""
    pass

class SizeLimitError(FileProcessorError):
    """File size limit exceeded"""
    pass

class UnsupportedFormatError(FileProcessorError):
    """Unsupported file format"""
    pass


class MissingDependencyError(FileProcessorError):
    """A parser or native prerequisite is unavailable."""
    pass


class ExtractionError(FileProcessorError):
    """A supported parser could not extract the file."""
    pass


class NoExtractableTextError(ExtractionError):
    """A valid document contains no embedded/selectable text."""
    pass


# ── Convenience Functions ─────────────────────────────────────────────

def parser_availability() -> Dict[str, dict]:
    """Return import-level parser truth without claiming extraction success."""
    return {
        "text": {"available": True, "dependency": "Python text I/O"},
        "pdf": {"available": HAS_PYPDF2, "dependency": "PyPDF2"},
        "docx": {"available": HAS_DOCX, "dependency": "python-docx"},
        "xlsx": {"available": HAS_OPENPYXL, "dependency": "openpyxl"},
        "image": {"available": HAS_PIL, "dependency": "Pillow"},
    }


def publish_parser_availability() -> Dict[str, dict]:
    """Publish parser presence as CONFIGURED/BLOCKED, never PROBED or LIVE."""
    availability = parser_availability()
    registry = get_registry()
    registry.set_evidence("FILE_PROCESSOR", EvidenceLevel.CONFIGURED,
                          "File processor loaded; extraction not yet tested",
                          source="file parser imports")
    mapping = {
        "text": ("FILE_TEXT_PARSER", "FILE_TEXT"),
        "pdf": ("FILE_PDF_PARSER", "FILE_PDF"),
        "docx": ("FILE_DOCX_PARSER", "FILE_DOCX"),
        "xlsx": ("FILE_XLSX_PARSER", "FILE_XLSX"),
        "image": ("FILE_IMAGE_PARSER", "FILE_IMAGE_OCR"),
    }
    for kind, info in availability.items():
        subsystem, capability = mapping[kind]
        evidence = EvidenceLevel.CONFIGURED if info["available"] else EvidenceLevel.BLOCKED
        detail = (f"{info['dependency']} import available; extraction not yet tested"
                  if info["available"] else f"{info['dependency']} is not installed")
        registry.set_evidence(subsystem, evidence, detail, source="file parser imports")
        registry.set_capability_evidence(capability, evidence, detail,
                                         source="file parser imports")
    return availability


_default_processor: Optional[FileProcessor] = None
_processor_lock = threading.Lock()

def get_processor() -> FileProcessor:
    """Get global file processor instance"""
    global _default_processor
    with _processor_lock:
        if _default_processor is None:
            _default_processor = FileProcessor()
        return _default_processor

def process_file(
    file_path: str,
    action: str,
    session_id: Optional[str] = None,
    **kwargs
) -> ProcessingResult:
    """Convenience function to process a file"""
    processor = get_processor()
    return processor.process_file(file_path, action, session_id, **kwargs)

def process_file_async(
    file_path: str,
    action: str,
    session_id: Optional[str] = None,
    callback: Optional[callable] = None,
    **kwargs
) -> str:
    """Submit file processing to task queue"""
    from task_queue import submit_task
    
    def _process():
        return process_file(file_path, action, session_id, **kwargs)
    
    task_id = asyncio.run(submit_task(_process, name=f"file_{action}_{os.path.basename(file_path)}"))
    
    if callback:
        # Schedule callback
        pass
    
    return task_id


# ── Testing ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import asyncio
    
    async def test_processor():
        processor = FileProcessor()
        
        # Create test files
        with tempfile.TemporaryDirectory() as tmpdir:
            # Test Python file
            py_file = Path(tmpdir) / "test.py"
            py_file.write_text("""
def hello(name):
    print(f"Hello, {name}!")
    
TODO: Add type hints
FIXME: Handle empty names

def unsafe_eval(code):
    return eval(code)
""")
            
            # Test actions
            print("=== INSPECT ===")
            result = processor.process_file(str(py_file), 'inspect')
            print(json.dumps(result.result, indent=2))
            
            print("\n=== SUMMARIZE ===")
            result = processor.process_file(str(py_file), 'summarize')
            print(result.result[:200])
            
            print("\n=== CODE REVIEW ===")
            result = processor.process_file(str(py_file), 'code_review')
            print(result.result)
            
            print("\n=== DEBUG ===")
            result = processor.process_file(str(py_file), 'debug')
            print(result.result)
            
            # Test CSV
            csv_file = Path(tmpdir) / "data.csv"
            csv_file.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago")
            print("\n=== CSV ANALYZE ===")
            result = processor.process_file(str(csv_file), 'analyze')
            print(json.dumps(result.result, indent=2))
            
            # Test text file
            txt_file = Path(tmpdir) / "notes.txt"
            txt_file.write_text("This is a test document.\nIt has multiple lines.\nFor testing extraction.")
            print("\n=== EXTRACT ===")
            result = processor.process_file(str(txt_file), 'extract')
            print(result.result)
    
    asyncio.run(test_processor())
