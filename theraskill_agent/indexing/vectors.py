"""Read immutable FAISS indexes without eagerly copying flat vectors to RAM."""
from __future__ import annotations


def read_faiss_index(path):
    import faiss
    # FAISS 1.13's IFC flag maps IndexFlatCodes storage, including IndexFlatIP.
    # IO_FLAG_MMAP alone only handles IVF storage. Older wheels retain their
    # normal reader; no new package is required by lexical deployments.
    # https://github.com/facebookresearch/faiss/blob/v1.13.2/faiss/index_io.h
    flag = getattr(faiss, 'IO_FLAG_MMAP_IFC', 0)
    return faiss.read_index(str(path), flag | faiss.IO_FLAG_READ_ONLY if flag else 0)
