from enum import Enum


class RunState(str,Enum):
    ANALYZING='analyzing'
    PLANNING='planning'
    AWAITING_APPROVAL='awaiting_approval'
    EXECUTING='executing'
    OBSERVING='observing'
    VERIFYING='verifying'
    COMPLETED='completed'
    FAILED='failed'
    CANCELLED='cancelled'
