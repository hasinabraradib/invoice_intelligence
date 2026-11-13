"""Split a document (e.g. a 1000-page scanned batch) into one segment per invoice."""

from app.segmentation.segmenter import Segment, segment_document

__all__ = ["Segment", "segment_document"]
