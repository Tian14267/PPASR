import json

import numpy as np
from paddle.io import Dataset

from ppasr.data_utils.audio import AudioSegment
from ppasr.data_utils.augmentor.augmentation import AugmentationPipeline
from ppasr.data_utils.featurizer.audio_featurizer import AudioFeaturizer
from ppasr.data_utils.featurizer.text_featurizer import TextFeaturizer
from ppasr.data_utils.normalizer import FeatureNormalizer
from ppasr.utils.logger import setup_logger

logger = setup_logger(__name__)


# 音频数据加载器
class PPASRDataset(Dataset):
    def __init__(self, data_list, vocab_filepath, mean_std_filepath, feature_method='linear',
                 min_duration=0, max_duration=20, augmentation_config='{}', train=False):
        super(PPASRDataset, self).__init__()
        self._normalizer = FeatureNormalizer(mean_std_filepath, feature_method=feature_method)
        self._augmentation_pipeline = AugmentationPipeline(augmentation_config=augmentation_config)
        self._audio_featurizer = AudioFeaturizer(feature_method=feature_method, train=train)
        self._text_featurizer = TextFeaturizer(vocab_filepath)
        # 获取数据列表
        with open(data_list, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        self.data_list = []
        for line in lines:
            line = json.loads(line)
            # 跳过超出长度限制的音频
            if line["duration"] < min_duration:
                continue
            if max_duration != -1 and line["duration"] > max_duration:
                continue
            self.data_list.append([line["audio_filepath"], line["text"]])

    def __getitem__(self, idx):
        try:
            # 分割音频路径和标签
            audio_file, transcript = self.data_list[idx]
            # 读取音频
            audio_segment = AudioSegment.from_file(audio_file)
            # 音频增强
            self._augmentation_pipeline.transform_audio(audio_segment)
            # 预处理，提取特征
            feature = self._audio_featurizer.featurize(audio_segment)
            transcript = self._text_featurizer.featurize(transcript)
            # 归一化
            feature = self._normalizer.apply(feature)
            # 特征增强
            feature = self._augmentation_pipeline.transform_feature(feature)
            transcript = np.array(transcript, dtype=np.int32)
            return feature.astype(np.float32), transcript
        except Exception as ex:
            logger.error("数据: {} 出错，错误信息: {}".format(self.data_list[idx], ex))
            rnd_idx = np.random.randint(self.__len__())
            return self.__getitem__(rnd_idx)

    def __len__(self):
        return len(self.data_list)

    @property
    def feature_dim(self):
        """返回词汇表大小

        :return: 词汇表大小
        :rtype: int
        """
        return self._audio_featurizer.feature_dim

    @property
    def vocab_size(self):
        """返回词汇表大小

        :return: 词汇表大小
        :rtype: int
        """
        return self._text_featurizer.vocab_size

    @property
    def vocab_list(self):
        """返回词汇表列表

        :return: 词汇表列表
        :rtype: list
        """
        return self._text_featurizer.vocab_list
