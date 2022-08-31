import numpy as np
import paddle
from paddleaudio.compliance.kaldi import mfcc, fbank

from ppasr.data_utils.audio import AudioSegment
from ppasr.data_utils.utils import delta


class AudioFeaturizer(object):
    """音频特征器,用于从AudioSegment或SpeechSegment内容中提取特性。

    Currently, it supports feature types of linear spectrogram and mfcc.

    :param stride_ms: Striding size (in milliseconds) for generating frames.
    :type stride_ms: float
    :param window_ms: Window size (in milliseconds) for generating frames.
    :type window_ms: float
    :param target_sample_rate: Audio are resampled (if upsampling or
                               downsampling is allowed) to this before
                               extracting spectrogram features.
    :type target_sample_rate: int
    :param use_dB_normalization: Whether to normalize the audio to a certain
                                 decibels before extracting the features.
    :type use_dB_normalization: bool
    :param target_dB: Target audio decibels for normalization.
    :type target_dB: float
    """

    def __init__(self,
                 feature_method='linear',
                 stride_ms=10.0,
                 window_ms=20.0,
                 target_sample_rate=16000,
                 use_dB_normalization=True,
                 target_dB=-20,
                 train=False):
        self._feature_method = feature_method
        self._stride_ms = stride_ms
        self._window_ms = window_ms
        self._target_sample_rate = target_sample_rate
        self._use_dB_normalization = use_dB_normalization
        self._target_dB = target_dB
        self.train = train

    def featurize(self, audio_segment, allow_downsampling=True, allow_upsampling=True):
        """从AudioSegment或SpeechSegment中提取音频特征

        :param audio_segment: Audio/speech segment to extract features from.
        :type audio_segment: AudioSegment|SpeechSegment
        :param allow_downsampling: Whether to allow audio downsampling before featurizing.
        :type allow_downsampling: bool
        :param allow_upsampling: Whether to allow audio upsampling before featurizing.
        :type allow_upsampling: bool
        :return: Spectrogram audio feature in 2darray.
        :rtype: ndarray
        :raises ValueError: If audio sample rate is not supported.
        """
        # upsampling or downsampling
        if ((audio_segment.sample_rate > self._target_sample_rate and
             allow_downsampling) or
                (audio_segment.sample_rate < self._target_sample_rate and
                 allow_upsampling)):
            audio_segment.resample(self._target_sample_rate)
        if audio_segment.sample_rate != self._target_sample_rate:
            raise ValueError("Audio sample rate is not supported. "
                             "Turn allow_downsampling or allow up_sampling on.")
        # decibel normalization
        if self._use_dB_normalization:
            audio_segment.normalize(target_db=self._target_dB)
        # extract spectrogram
        if self._feature_method == 'linear':
            return self._compute_linear(audio_segment.samples, audio_segment.sample_rate,
                                        stride_ms=self._stride_ms, window_ms=self._window_ms)
        elif self._feature_method == 'mfcc':
            samples = audio_segment.to('int16')
            return self._compute_mfcc(samples=samples, sample_rate=audio_segment.sample_rate)
        elif self._feature_method == 'fbank':
            samples = audio_segment.to('int16')
            return self._compute_fbank(samples=samples, sample_rate=audio_segment.sample_rate)
        else:
            raise Exception('没有{}预处理方法'.format(self._feature_method))

    # 用快速傅里叶变换计算线性谱图
    @staticmethod
    def _compute_linear(samples, sample_rate, stride_ms=10.0, window_ms=20.0, eps=1e-14):
        stride_size = int(0.001 * sample_rate * stride_ms)
        window_size = int(0.001 * sample_rate * window_ms)
        truncate_size = (len(samples) - window_size) % stride_size
        samples = samples[:len(samples) - truncate_size]
        nshape = (window_size, (len(samples) - window_size) // stride_size + 1)
        nstrides = (samples.strides[0], samples.strides[0] * stride_size)
        windows = np.lib.stride_tricks.as_strided(samples, shape=nshape, strides=nstrides)
        assert np.all(windows[:, 1] == samples[stride_size:(stride_size + window_size)])
        # 快速傅里叶变换
        weighting = np.hanning(window_size)[:, None]
        fft = np.fft.rfft(windows * weighting, n=None, axis=0)
        fft = np.absolute(fft)
        fft = fft ** 2
        scale = np.sum(weighting ** 2) * sample_rate
        fft[1:-1, :] *= (2.0 / scale)
        fft[(0, -1), :] /= scale
        freqs = float(sample_rate) / window_size * np.arange(fft.shape[0])
        ind = np.where(freqs <= (sample_rate / 2))[0][-1] + 1
        linear_feat = np.log(fft[:ind, :] + eps)  # dim=161
        return linear_feat

    def _compute_mfcc(self,
                      samples,
                      sample_rate,
                      n_mels=161,
                      n_shift=160,
                      win_length=400,
                      energy_floor=0.0,
                      dither=0.1):
        num_point_ms = sample_rate / 1000
        n_frame_length = win_length / num_point_ms
        n_frame_shift = n_shift / num_point_ms

        dither = dither if self.train else 0.0
        waveform = paddle.to_tensor(np.expand_dims(samples, 0), dtype=paddle.float64)
        # 计算MFCC
        mfcc_feat = mfcc(waveform,
                         n_mels=n_mels,
                         frame_length=n_frame_length,
                         frame_shift=n_frame_shift,
                         dither=dither,
                         energy_floor=energy_floor,
                         sr=sample_rate)
        mfcc_feat = mfcc_feat.numpy()
        # Deltas
        d_feat = delta(mfcc_feat, 2)
        # Deltas-Deltas
        dd_feat = delta(mfcc_feat, 2)
        # concat above three features
        mfcc_feat = np.concatenate((mfcc_feat, d_feat, dd_feat), axis=1)  # dim=39
        mfcc_feat = mfcc_feat.transpose([1, 0])
        return mfcc_feat

    def _compute_fbank(self,
                       samples,
                       sample_rate,
                       n_mels=161,
                       n_shift=160,
                       win_length=400,
                       energy_floor=0.0,
                       dither=0.1):
        num_point_ms = sample_rate / 1000
        n_frame_length = win_length / num_point_ms
        n_frame_shift = n_shift / num_point_ms

        dither = dither if self.train else 0.0
        waveform = paddle.to_tensor(np.expand_dims(samples, 0), dtype=paddle.float64)
        # 计算Fbank
        mat = fbank(waveform,
                    n_mels=n_mels,
                    frame_length=n_frame_length,
                    frame_shift=n_frame_shift,
                    dither=dither,
                    energy_floor=energy_floor,
                    sr=sample_rate)
        mat = mat.transpose((1, 0))  # dim=161
        fbank_feat = mat.numpy()
        return fbank_feat

    @property
    def feature_dim(self):
        """返回特征大小

        :return: 特征大小
        :rtype: int
        """
        if self._feature_method == 'linear':
            return 161
        elif self._feature_method == 'mfcc':
            return 39
        elif self._feature_method == 'fbank':
            return 161
        else:
            raise Exception('没有{}预处理方法'.format(self._feature_method))
