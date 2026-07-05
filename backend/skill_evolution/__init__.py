"""Self-evolving skills: an offline batch pipeline that builds eval datasets
from real skill usage and (later stages) runs DSPy+GEPA optimization against
them. Deliberately a sibling package to agent_runner/, not folded into it or
into app/services/ — see dataset.py's module docstring for why.
"""
