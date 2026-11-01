import math

from fleck_helper.probe import rms, tone_samples


def test_rms_of_silence_and_sine():
    assert rms([]) == 0.0
    assert rms([0.0] * 10) == 0.0
    assert abs(rms(tone_samples(0.5, 440, 16000, 1.0)) - 0.5 / math.sqrt(2)) < 0.01
