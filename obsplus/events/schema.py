"""
Pydantic schema for ObsPy's event model.
"""
from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import uuid4

import obspy.core.event as ev
from pydantic import BaseModel, validator, root_validator
from typing_extensions import Literal

from obsplus.constants import NSLC

# ----- Type Literals (enum like, but simple)


data_used_wave_type = Literal[
    "P waves", "body waves", "surface waves", "mantle waves", "combined", "unknown"
]

AmplitudeCategory = Literal["point", "mean", "duration", "period", "integral", "other"]

AmplitudeUnit = Literal["m", "s", "m/s", "m/(s*s)", "m*s", "dimensionless", "other"]

DataUsedWaveType = Literal[
    "P waves", "body waves", "surface waves", "mantle waves", "combined", "unknown"
]

EvaluationMode = Literal["manual", "automatic"]

EvaluationStatus = Literal["preliminary", "confirmed", "reviewed", "final", "rejected"]

EventDescriptionType = Literal[
    "felt report",
    "Flinn-Engdahl region",
    "local time",
    "tectonic summary",
    "nearest cities",
    "earthquake name",
    "region name",
]

EventType = Literal[
    "not existing",
    "not reported",
    "earthquake",
    "anthropogenic event",
    "collapse",
    "cavity collapse",
    "mine collapse",
    "building collapse",
    "explosion",
    "accidental explosion",
    "chemical explosion",
    "controlled explosion",
    "experimental explosion",
    "industrial explosion",
    "mining explosion",
    "quarry blast",
    "road cut",
    "blasting levee",
    "nuclear explosion",
    "induced or triggered event",
    "rock burst",
    "reservoir loading",
    "fluid injection",
    "fluid extraction",
    "crash",
    "plane crash",
    "train crash",
    "boat crash",
    "other event",
    "atmospheric event",
    "sonic boom",
    "sonic blast",
    "acoustic noise",
    "thunder",
    "avalanche",
    "snow avalanche",
    "debris avalanche",
    "hydroacoustic event",
    "ice quake",
    "slide",
    "landslide",
    "rockslide",
    "meteorite",
    "volcanic eruption",
]

EventTypeCertainty = Literal["known", "suspected"]

MTInversionType = Literal["general", "zero trace", "double couple"]

MomentTensorCategory = Literal["teleseismic", "regional"]

OriginDepthType = Literal[
    "from location",
    "from moment tensor inversion",
    "from modeling of broad-band P waveforms",
    "constrained by depth phases",
    "constrained by direct phases",
    "constrained by depth and direct phases",
    "operator assigned",
    "other",
]

OriginType = Literal[
    "hypocenter",
    "centroid",
    "amplitude",
    "macroseismic",
    "rupture start",
    "rupture end",
]

OriginUncertaintyDescription = Literal[
    "horizontal uncertainty", "uncertainty ellipse", "confidence ellipsoid"
]

PickOnset = Literal["emergent", "impulsive", "questionable"]

PickPolarity = Literal["positive", "negative", "undecidable"]

SourceTimeFunctionType = Literal["box car", "triangle", "trapezoid", "unknown"]


# ----- Type Models


class _ObsPyModel(BaseModel):
    extra: Optional[Dict[str, Any]] = None

    class Config:
        pass
        validate_assignment = True
        arbitrary_types_allowed = True
        orm_mode = True
        extra = "allow"


class ResourceIdentifier(_ObsPyModel):
    id: Optional[str] = None

    # referred_object: Optional[Any] = None
    # prefix: Optional[str] = None
    # _base = ev.ResourceIdentifier

    @root_validator(pre=True)
    def get_id(cls, values):
        value = values.get("id")
        if value is None:
            value = str(uuid4())
        return {"id": value}


class _ModelWithResourceID(_ObsPyModel):
    resource_id: Optional[ResourceIdentifier]

    # @validator("resource_id", always=True)
    # def get_resource_id(cls, value):
    #     return ResourceIdentifier(id=value)


class QuantityError(_ObsPyModel):
    uncertainty: Optional[float] = None
    lower_uncertainty: Optional[float] = None
    upper_uncertainty: Optional[float] = None
    confidence_level: Optional[float] = None
    _base = ev.QuantityError


class CreationInfo(_ObsPyModel):
    agency_id: Optional[str] = None
    agency_uri: Optional[ResourceIdentifier] = None
    author: Optional[str] = None
    author_uri: Optional[ResourceIdentifier] = None
    creation_time: Optional[datetime] = None
    version: Optional[str] = None
    _base = ev.CreationInfo


class TimeWindow(_ObsPyModel):
    begin: Optional[float] = None
    end: Optional[float] = None
    reference: Optional[datetime] = None
    _base = ev.TimeWindow


class CompositeTime(_ObsPyModel):
    year: Optional[int]
    year_errors: Optional[QuantityError]
    month: Optional[int]
    month_errors: Optional[QuantityError]
    day: Optional[int]
    day_errors: Optional[QuantityError]
    hour: Optional[int]
    hour_errors: Optional[QuantityError]
    minute: Optional[int]
    minute_errors: Optional[QuantityError]
    second: Optional[float]
    second_errors: Optional[QuantityError]
    _base = ev.CompositeTime


class Comment(_ModelWithResourceID):
    text: Optional[str] = None
    creation_info: Optional[CreationInfo] = None
    _base = ev.Comment


class WaveformStreamID(_ObsPyModel):
    network_code: Optional[str] = None
    station_code: Optional[str] = None
    location_code: Optional[str] = None
    channel_code: Optional[str] = None
    resource_uri: Optional[ResourceIdentifier] = None
    seed_string: Optional[str] = None
    _base = ev.WaveformStreamID

    @validator("seed_string")
    def validate_seed_str(cls, value):
        split = value.split(".")
        assert len(split) == 4
        return value

    @root_validator()
    def parse_seed_id(cls, values):
        """Parse seed IDs if needed."""
        seed_str = values.get("seed_string", None)
        if not seed_str:
            return values
        split = seed_str.split(".")
        for code, name in zip(split, NSLC):
            values[f"{name}_code"] = code
        return values


class ConfidenceEllipsoid(_ObsPyModel):
    semi_major_axis_length: Optional[float] = None
    semi_minor_axis_length: Optional[float] = None
    semi_intermediate_axis_length: Optional[float] = None
    major_axis_plunge: Optional[float] = None
    major_axis_azimuth: Optional[float] = None
    major_axis_rotation: Optional[float] = None
    _base = ev.WaveformStreamID


class DataUsed(_ObsPyModel):
    wave_type: Optional[DataUsedWaveType] = None
    station_count: Optional[int] = None
    component_count: Optional[int] = None
    shortest_period: Optional[float] = None
    longest_period: Optional[float] = None
    _baser = ev.DataUsed


# --- Magnitude classes


class StationMagnitude(_ModelWithResourceID):
    origin_id: Optional[ResourceIdentifier] = None
    mag: Optional[float] = None
    mag_errors: Optional[QuantityError] = None
    station_magnitude_type: Optional[str] = None
    amplitude_id: Optional[ResourceIdentifier] = None
    method_id: Optional[ResourceIdentifier] = None
    waveform_id: Optional[WaveformStreamID] = None
    creation_info: Optional[CreationInfo] = None

    comments: List[Comment] = []


class StationMagnitudeContribution(_ObsPyModel):
    station_magnitude_id: Optional[ResourceIdentifier] = None
    residual: Optional[float] = None
    weight: Optional[float] = None


class Amplitude(_ModelWithResourceID):
    generic_amplitude: Optional[float] = None
    generic_amplitude_errors: Optional[QuantityError] = None
    type: Optional[str] = None
    category: Optional[AmplitudeCategory] = None
    unit: Optional[AmplitudeUnit] = None
    method_id: Optional[ResourceIdentifier] = None
    period: Optional[float] = None
    period_errors: Optional[QuantityError] = None
    snr: Optional[float] = None
    time_window: Optional[TimeWindow] = None
    pick_id: Optional[ResourceIdentifier] = None
    waveform_id: Optional[WaveformStreamID] = None
    scaling_time: Optional[datetime] = None
    scaling_time_errors: Optional[QuantityError] = None
    magnitude_hint: Optional[str] = None
    evaluation_mode: Optional[EvaluationMode] = None
    evaluation_status: Optional[EvaluationStatus] = None
    creation_info: Optional[CreationInfo] = None
    comments = List[Comment]


# --- Origin classes


class OriginUncertainty(_ObsPyModel):
    horizontal_uncertainty: Optional[float] = None
    min_horizontal_uncertainty: Optional[float] = None
    max_horizontal_uncertainty: Optional[float] = None
    azimuth_max_horizontal_uncertainty: Optional[float] = None
    confidence_ellipsoid: Optional[ConfidenceEllipsoid] = None
    preferred_description: Optional[OriginUncertaintyDescription] = None
    confidence_level: Optional[float] = None


class OriginQuality(_ObsPyModel):
    associated_phase_count: Optional[int] = None
    used_phase_count: Optional[int] = None
    associated_station_count: Optional[int] = None
    used_station_count: Optional[int] = None
    depth_phase_count: Optional[int] = None
    standard_error: Optional[int] = None
    azimuthal_gap: Optional[int] = None
    secondary_azimuthal_gap: Optional[int] = None
    ground_truth_level: Optional[str] = None
    minimum_distance: Optional[float] = None
    maximum_distance: Optional[float] = None
    median_distance: Optional[float] = None


class Pick(_ModelWithResourceID):
    time: Optional[datetime] = None
    time_errors: Optional[QuantityError] = None
    waveform_id: Optional[WaveformStreamID] = None
    filter_id: Optional[ResourceIdentifier] = None
    method_id: Optional[ResourceIdentifier] = None
    horizontal_slowness: Optional[float] = None
    horizontal_slowness_errors: Optional[QuantityError] = None
    backazimuth: Optional[float] = None
    backazimuth_errors: Optional[QuantityError] = None
    slowness_method_id: Optional[ResourceIdentifier] = None
    onset: Optional[PickOnset] = None
    phase_hint: Optional[str] = None
    polarity: Optional[PickPolarity] = None
    evaluation_mode: Optional[EvaluationMode] = None
    evaluation_status: Optional[EvaluationStatus] = None
    creation_info: Optional[CreationInfo] = None

    comments: List[Comment] = []


class Arrival(_ModelWithResourceID):
    pick_id: Optional[ResourceIdentifier] = None
    phase: Optional[str] = None
    time_correction: Optional[float] = None
    azimuth: Optional[float] = None
    distance: Optional[float] = None
    takeoff_angle: Optional[float] = None
    takeoff_angle_errors: Optional[QuantityError] = None
    time_residual: Optional[float] = None
    horizontal_slowness_residual: Optional[float] = None
    backazimuth_residual: Optional[float] = None
    time_weight: Optional[float] = None
    horizontal_slowness_weight: Optional[float] = None
    backazimuth_weight: Optional[float] = None
    earth_model_id: Optional[ResourceIdentifier] = None
    creation_info: Optional[CreationInfo] = None

    comments: List[Comment] = []


class Origin(_ModelWithResourceID):
    time: datetime
    longitude: Optional[int] = None
    latitude: Optional[int] = None
    depth: Optional[int] = None
    depth_type: Optional[OriginDepthType] = None
    time_fixed: Optional[bool] = None
    epicenter_fixed: Optional[bool] = None
    reference_system_id: Optional[ResourceIdentifier] = None
    method_id: Optional[ResourceIdentifier] = None
    earth_model_id: Optional[ResourceIdentifier] = None
    quality: Optional[OriginQuality] = None
    origin_type: Optional[OriginType] = None
    origin_uncertainty: Optional[OriginUncertainty]
    region: Optional[str] = None
    evaluation_mode: Optional[EvaluationMode] = None
    evaluation_status: Optional[EvaluationStatus] = None
    creation_info: Optional[CreationInfo] = None

    comments: List[Comment] = []
    arrivals: List[Arrival] = []
    composite_times: List[CompositeTime] = []


class Magnitude(_ModelWithResourceID):
    mag: Optional[float] = None
    mag_errors: Optional[QuantityError] = None
    magnitude_type: Optional[str] = None
    origin_id: Optional[ResourceIdentifier] = None
    method_id: Optional[ResourceIdentifier] = None
    station_count: Optional[int] = None
    azimuthal_gap: Optional[float] = None
    evaluation_mode: Optional[EvaluationMode] = None
    evaluation_status: Optional[EvaluationStatus] = None
    creation_info: Optional[CreationInfo] = None
    comments: List[Comment] = []
    station_magnitude_contributions: List[StationMagnitudeContribution] = []

    _base = ev.Magnitude


# --- Source objects


class Axis(_ObsPyModel):
    azimuth: Optional[float] = None
    plunge: Optional[float] = None
    length: Optional[float] = None


class NodalPlane(_ObsPyModel):
    strike: Optional[float] = None
    dip: Optional[float] = None
    rake: Optional[float] = None


class NodalPlanes(_ObsPyModel):
    nodal_plane_1: Optional[NodalPlane] = None
    nodal_plane_2: Optional[NodalPlane] = None
    preferred_plane: Optional[int] = None


class PrincipalAxes(_ObsPyModel):
    t_axis: Optional[Axis] = None
    p_axis: Optional[Axis] = None
    n_axis: Optional[Axis] = None


class Tensor(_ObsPyModel):
    m_rr: Optional[float] = None
    m_rr_errors: Optional[QuantityError] = None
    m_tt: Optional[float] = None
    m_tt_errors: Optional[QuantityError] = None
    m_pp: Optional[float] = None
    m_pp_errors: Optional[QuantityError] = None
    m_rt: Optional[float] = None
    m_rt_errors: Optional[QuantityError] = None
    m_rp: Optional[float] = None
    m_rp_errors: Optional[QuantityError] = None
    m_tp: Optional[float] = None
    m_tp_errors: Optional[QuantityError] = None


class SourceTimeFunction(_ObsPyModel):
    type: Optional[SourceTimeFunctionType] = None
    duration: Optional[float] = None
    rise_time: Optional[float] = None
    decay_time: Optional[float] = None


class MomentTensor(_ModelWithResourceID):
    derived_origin_id: Optional[ResourceIdentifier] = None
    moment_magnitude_id: Optional[ResourceIdentifier] = None
    scalar_moment: Optional[float] = None
    scalar_moment_errors: Optional[QuantityError] = None
    tensor: Optional[Tensor] = None
    variance: Optional[float] = None
    variance_reduction: Optional[float] = None
    double_couple: Optional[float] = None
    clvd: Optional[float] = None
    iso: Optional[float] = None
    greens_function_id: Optional[float] = None
    filter_id: Optional[ResourceIdentifier] = None
    source_time_function: Optional[SourceTimeFunction] = None
    method_id: Optional[ResourceIdentifier] = None
    category: Optional[MomentTensorCategory] = None
    inversion_type: Optional[MTInversionType] = None
    creation_info: Optional[CreationInfo] = None


class FocalMechanism(_ModelWithResourceID):
    triggering_origin_id: Optional[ResourceIdentifier] = None
    nodal_planes: Optional[NodalPlanes] = None
    principal_axes: Optional[PrincipalAxes] = None
    azimuthal_gap: Optional[float] = None
    station_polarity_count: Optional[int] = None
    misfit: Optional[float] = None
    station_distribution_ratio: Optional[float] = None
    method_id: Optional[ResourceIdentifier] = None
    evaluation_mode: Optional[EvaluationMode] = None
    evaluation_status: Optional[EvaluationStatus] = None
    moment_tensor: Optional[MomentTensor] = None
    creation_info: Optional[CreationInfo] = None

    waveform_id: List[WaveformStreamID] = []
    comments: List[Comment] = []


# --- Event definitions


class EventDescription(_ObsPyModel):
    text: Optional[str] = None
    type: Optional[EventDescriptionType] = None


class Event(_ModelWithResourceID):
    event_type: Optional[EventType] = None
    event_type_certainty: Optional[EventTypeCertainty] = None
    creation_info: Optional[CreationInfo] = None
    preferred_origin_id: Optional[ResourceIdentifier] = None
    preferred_magnitude_id: Optional[ResourceIdentifier] = None
    preferred_focal_mechanism_id: Optional[ResourceIdentifier] = None

    event_description: List[EventDescription] = None
    comments: List[Comment] = []
    picks: List[Pick] = []
    amplitudes: List[Amplitude] = []
    focal_mechanisms: List[FocalMechanism] = []
    origins: List[Origin] = []
    magnitudes: List[Magnitude] = []
    station_magnitudes: List[StationMagnitude] = []


class Catalog(_ModelWithResourceID):
    """A collection of events."""

    events: List[Event] = []
    description: Optional[str] = None
    comments: Optional[List[Comment]] = None
    creation_info: Optional[CreationInfo] = None
