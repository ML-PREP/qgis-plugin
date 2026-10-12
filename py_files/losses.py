import tensorflow as tf


def dice(y_true, y_pred, smooth):
    y_true = tf.cast(y_true, tf.float32)
    y_pred = tf.cast(y_pred, tf.float32)

    y_true = tf.reshape(y_true, [-1])
    y_pred = tf.reshape(y_pred, [-1])

    intersection = tf.reduce_sum(y_true * y_pred)
    pred_sum = tf.reduce_sum(y_pred)
    true_sum = tf.reduce_sum(y_true)

    dice_value = (2.0 * intersection + smooth) / (pred_sum + true_sum + smooth)
    return 1 - dice_value


def weighted_bce(y_true, y_pred, a):
    y_true = tf.cast(y_true, tf.float32)
    y_pred = tf.clip_by_value(y_pred, 1e-7, 1 - 1e-7)

    first_term = (1 - a) * y_true * tf.math.log(y_pred)
    second_term = a * (1 - y_true) * tf.math.log(1 - y_pred)
    loss = -(first_term + second_term)

    reduced_loss = tf.reduce_mean(loss)
    return reduced_loss


@tf.keras.utils.register_keras_serializable()
class DiceCrossEntropyLoss(tf.keras.losses.Loss):
    def __init__(
        self,
        a=0.25,
        b=0.5,
        smooth=1e-06,
        name="DiceCELoss",
        reduction="sum_over_batch_size",
        n_l=1.0,
    ):
        super().__init__(name=name)
        self.a = a
        self.b = b
        self.smooth = smooth

    def call(self, y_true, y_pred):
        bce = weighted_bce(y_true, y_pred, self.a)
        dice_loss = dice(y_true, y_pred, self.smooth)
        return self.b * bce + (1 - self.b) * dice_loss

    def get_config(self):
        config = super().get_config()
        config.update({"name": self.name})
        return config
