"""
Tests for event schema.
"""
from pathlib import Path
from functools import lru_cache
from typing import Union, Optional, List, Mapping, Dict, Any

import matplotlib.pyplot as plt
import numpy as np
import scipy
import obsplus
import obspy
import pandas as pd
import pytest
import obspy.core.event as ev

import obsplus.events.schema as esc
from obsplus.constants import NSLC


class TestResourceID:
    def test_null(self):
        rid = esc.ResourceIdentifier()
        assert isinstance(rid.id, str)
        assert len(rid.id)

    def test_defined_resource_id(self):
        """Ensure the defined resource_id sticks."""
        rid = str(ev.ResourceIdentifier())
        out = esc.ResourceIdentifier(id=rid)
        assert out.id == rid


class TestWaveformID:
    def test_seed_id(self):
        seed_id = "UU.TMU.01.HHZ"
        out = esc.WaveformStreamID(seed_string=seed_id)
        for name, value in zip(NSLC, seed_id.split(".")):
            assert getattr(out, f"{name}_code") == value


class TestEvent:
    def test_resource_id(self):
        """Ensure the ResourceID gets created."""
        out = esc.Event()
        assert out.resource_id is not None

    def test_non_mutable_defaults(self):
        """
        Ensure appending to a default list in one instances doesn't effect others.
        """
        ev1 = esc.Event()
        ev2 = esc.Event()
        ev1.comments.append("bob")
        assert "bob" in ev1.comments
        assert not len(ev2.comments)
