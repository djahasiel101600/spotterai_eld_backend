"""
US FMCSA Hours of Service Configuration for Property-carrying (Interstate)
Based on 49 CFR Part 395 - Hours of Service of Drivers
"""

HOS_PROFILE_CONFIG_PROPERTY_CARRYING_US_FMCSA = {
    "profileId": "us_fmcsa_property_carrying_interstate",
    "label": "US FMCSA – Property-carrying (Interstate)",
    
    "dutyStatuses": {
        "off_duty": {
            "countsTowardDriving": False,
            "countsTowardOnDuty": False,
            "countsTowardDutyWindow": False,
            "qualifiesAsRest": True,
        },
        "sleeper_berth": {
            "countsTowardDriving": False,
            "countsTowardOnDuty": False,
            "countsTowardDutyWindow": False,
            "qualifiesAsRest": True,
        },
        "driving": {
            "countsTowardDriving": True,
            "countsTowardOnDuty": True,
            "countsTowardDutyWindow": True,
            "qualifiesAsRest": False,
        },
        "on_duty_not_driving": {
            "countsTowardDriving": False,
            "countsTowardOnDuty": True,
            "countsTowardDutyWindow": True,
            "qualifiesAsRest": False,
        },
    },
    
    "shiftRules": {
        "minOffDutyBeforeShiftHours": 10,  # 10 hours off-duty required before new shift
        "maxDrivingHours": 11,              # 11-hour driving limit
        "maxDutyWindowHours": 14,           # 14-hour duty window
        "allowDrivingAfterDutyWindow": False,  # Cannot drive after 14 hours
    },
    
    "breakRules": {
        "requiredAfterDrivingHours": 8,     # 30-min break required after 8 hours driving
        "breakDurationMinutes": 30,         # Minimum 30 minutes
        "qualifyingStatuses": ["off_duty", "sleeper_berth", "on_duty_not_driving"],
        "mustBeContinuous": True,           # Break must be continuous (not split)
    },
    
    "cycleRules": {
        "supportedCycles": [
            {
                "id": "60_in_7",
                "label": "60 hours / 7 days",
                "maxOnDutyHours": 60,
                "periodDays": 7
            },
            {
                "id": "70_in_8",
                "label": "70 hours / 8 days",
                "maxOnDutyHours": 70,
                "periodDays": 8
            },
        ],
        "defaultCycleId": "70_in_8",        # Most common cycle
        "restart": {
            "enabled": True,
            "durationHours": 34,            # 34-hour restart provision
            "mustBeContinuous": True,       # Must be continuous off-duty time
        },
    },
    
    "sleeperBerthRules": {
        "enabled": True,
        "splitOptions": [
            {
                # 8/2 split: 8 hours sleeper + 2 hours off-duty/sleeper
                "sleeperMinHours": 8,
                "otherMinHours": 2,
                "otherAllowedStatuses": ["off_duty", "sleeper_berth"],
            },
            {
                # 7/3 split: 7 hours sleeper + 3 hours off-duty/sleeper
                "sleeperMinHours": 7,
                "otherMinHours": 3,
                "otherAllowedStatuses": ["off_duty", "sleeper_berth"],
            },
        ],
        "sleeperStatus": "sleeper_berth",
        "allowSplitToPauseDutyWindow": True,      # Split can pause 14-hour window
        "allowSplitToSatisfyShiftOffDuty": True,  # Split can satisfy 10-hour requirement
    },
    
    "exceptions": {
        "adverseDrivingConditions": {
            "enabled": True,
            "extraDrivingHours": 2,         # Can extend by 2 hours in adverse conditions
            "extraDutyWindowHours": 2,      # Can extend 14-hour window by 2 hours
        },
        "shortHaul": {
            "enabled": False,               # Short haul exception (usually not used for interstate)
            "maxRadiusAirMiles": 150,       # 150 air-mile radius
            "maxDutyHours": 14,
            "maxDrivingHours": 11,
            "logbookNotRequired": True,     # No ELD required if conditions met
        },
    },
    
    "timeRules": {
        "timezoneStrategy": "home_terminal",   # Use home terminal timezone
        "precision": "minute",                  # Round to nearest minute (15-min intervals for ELD)
        "rollingWindows": True,                 # Use rolling 7/8 day windows
        "requireAuditTrailForEdits": True,     # Edits must be audited
    },
    
    "alerts": {
        "approaching": [
            {"type": "driving", "thresholdMinutes": 60},        # Alert 1 hour before 11-hour limit
            {"type": "duty_window", "thresholdMinutes": 60},    # Alert 1 hour before 14-hour limit
            {"type": "break", "thresholdMinutes": 30},          # Alert 30 min before 8-hour limit
            {"type": "cycle", "thresholdMinutes": 120},         # Alert 2 hours before cycle limit
        ],
    },
}


# Default configuration alias
DEFAULT_HOS_CONFIG = HOS_PROFILE_CONFIG_PROPERTY_CARRYING_US_FMCSA


def get_hos_config(profile_id=None):
    """
    Get HOS configuration by profile ID.
    Currently only supports FMCSA property-carrying.
    
    Args:
        profile_id: Optional profile ID. Defaults to FMCSA property-carrying.
        
    Returns:
        dict: HOS configuration
    """
    # For now, we only have one profile
    # In the future, this could load from database or support multiple profiles
    return DEFAULT_HOS_CONFIG.copy()


def validate_hos_config(config):
    """
    Validate that HOS configuration has all required fields.
    
    Args:
        config: HOS configuration dictionary
        
    Returns:
        tuple: (is_valid, error_message)
    """
    required_keys = [
        'profileId', 'label', 'dutyStatuses', 'shiftRules', 
        'breakRules', 'cycleRules'
    ]
    
    for key in required_keys:
        if key not in config:
            return False, f"Missing required key: {key}"
    
    # Validate shift rules
    shift_rules = config.get('shiftRules', {})
    required_shift_keys = ['minOffDutyBeforeShiftHours', 'maxDrivingHours', 'maxDutyWindowHours']
    for key in required_shift_keys:
        if key not in shift_rules:
            return False, f"Missing required shiftRules key: {key}"
    
    # Validate break rules
    break_rules = config.get('breakRules', {})
    required_break_keys = ['requiredAfterDrivingHours', 'breakDurationMinutes']
    for key in required_break_keys:
        if key not in break_rules:
            return False, f"Missing required breakRules key: {key}"
    
    return True, None


def get_max_driving_hours(config):
    """Extract max driving hours from config"""
    return config.get('shiftRules', {}).get('maxDrivingHours', 11)


def get_max_duty_window_hours(config):
    """Extract max duty window hours from config"""
    return config.get('shiftRules', {}).get('maxDutyWindowHours', 14)


def get_required_break_after_hours(config):
    """Extract required break after driving hours from config"""
    return config.get('breakRules', {}).get('requiredAfterDrivingHours', 8)


def get_break_duration_minutes(config):
    """Extract break duration in minutes from config"""
    return config.get('breakRules', {}).get('breakDurationMinutes', 30)


def get_min_off_duty_hours(config):
    """Extract minimum off-duty hours before shift from config"""
    return config.get('shiftRules', {}).get('minOffDutyBeforeShiftHours', 10)


def get_default_cycle(config):
    """Extract default cycle configuration from config"""
    cycle_rules = config.get('cycleRules', {})
    default_cycle_id = cycle_rules.get('defaultCycleId', '70_in_8')
    
    for cycle in cycle_rules.get('supportedCycles', []):
        if cycle['id'] == default_cycle_id:
            return cycle
    
    # Fallback to 70/8 if not found
    return {
        "id": "70_in_8",
        "label": "70 hours / 8 days",
        "maxOnDutyHours": 70,
        "periodDays": 8
    }
