import tensorflow as tf
from tensorflow.contrib.layers import batch_norm
import numpy as np
import math
import sklearn


class HighwayConvClassifier:
    def __init__(self, img_size, img_ch, pool_size, n_out, sess):
        """
        Parameters:
        -----------
        img_size: tuple
            (height, width) of the image size
        img_ch: int
            Number of image channel
        pool_size: int
            Size of the max pooling windows (assumed square window)
        n_out: int
            Output dimensions
        sess: object
            tf.Session() object 
        """
        self.img_size = img_size
        self.img_ch = img_ch
        self.pool_size = pool_size
        self.n_out = n_out
        self.sess = sess
        self.current_layer = None
        self.build_graph()
    # end constructor


    def build_graph(self):
        self.add_input_layer()

        self.add_conv('conv1', filter_shape=[5,5,self.img_ch,32])
        self.add_highway('highway1', filter_shape=[3,3,32,32])
        self.add_highway('highway2', filter_shape=[3,3,32,32])
        self.add_maxpool(k = self.pool_size)

        self.add_conv('conv2', filter_shape=[5,5,32,64])
        self.add_highway('highway3', filter_shape=[3,3,64,64])
        self.add_highway('highway4', filter_shape=[3,3,64,64])
        self.add_maxpool(k = self.pool_size)

        self.add_fc('fc1', [int(self.img_size[0]/4)*int(self.img_size[1]/4)*64,512], flatten_input=True)

        self.add_output_layer(in_dim=512)   
        self.add_backward_path()
    # end method build_graph


    def add_input_layer(self):
        self.X = tf.placeholder(tf.float32, [None, self.img_size[0], self.img_size[1], self.img_ch])
        self.Y = tf.placeholder(tf.float32, [None, self.n_out])
        self.keep_prob = tf.placeholder(tf.float32)
        self.current_layer = self.X
    # end method add_input_layer


    def add_conv(self, name, filter_shape, strides=1):
        W = self._W(name+'_w', filter_shape)
        b = self._b(name+'_b', [filter_shape[-1]])
        conv = tf.nn.conv2d(self.current_layer, W, strides=[1,strides,strides,1], padding='SAME')
        conv = tf.nn.bias_add(conv, b)
        conv = batch_norm(conv)
        conv = tf.nn.relu(conv)
        self.current_layer = conv
    # end method add_conv_layer


    def add_highway(self, name, filter_shape, carry_bias=-1.0):
        W = tf.get_variable(name+'_w', filter_shape, tf.float32, tf.truncated_normal_initializer(stddev=0.1))
        b = tf.get_variable(name+'_b', filter_shape[-1], tf.float32, tf.constant_initializer(carry_bias))
        W_T = tf.get_variable(name+'_wt', filter_shape, tf.float32, tf.truncated_normal_initializer(stddev=0.1))
        b_T = tf.get_variable(name+'_bt', filter_shape[-1], tf.float32, tf.constant_initializer(0.1))

        H = tf.nn.relu(batch_norm(tf.nn.bias_add(tf.nn.conv2d(self.current_layer,W,[1,1,1,1],'SAME'), b)))
        T = tf.sigmoid(batch_norm(tf.nn.bias_add(tf.nn.conv2d(self.current_layer,W_T,[1,1,1,1],'SAME'), b_T)),
                       name='transform_gate')
        C = tf.subtract(1.0, T, name="carry_gate")
        
        self.current_layer = tf.add(tf.multiply(H,T), tf.multiply(self.current_layer,C)) # y = (H * T) + (x * C)
    # end method add_conv_highway


    def add_maxpool(self, k=2):
        self.current_layer = tf.nn.max_pool(self.current_layer, ksize=[1, k, k, 1], strides=[1, k, k, 1],
                                            padding='SAME')
    # end method add_maxpool


    def add_fc(self, name, w_shape, flatten_input=False):
        W = self._W(name+'_w', w_shape)
        b = self._b(name+'_b', [w_shape[-1]])
        fc = tf.reshape(self.current_layer, [-1, w_shape[0]]) if flatten_input else self.current_layer
        fc = tf.nn.bias_add(tf.matmul(fc, W), b)
        fc = batch_norm(fc)
        fc = tf.nn.relu(fc)
        fc = tf.nn.dropout(fc, self.keep_prob)
        self.current_layer = fc
    # end method add_fully_connected_layer


    def add_output_layer(self, in_dim):
        self.logits = tf.nn.bias_add(tf.matmul(self.current_layer, self._W('w_out', [in_dim,self.n_out])),
                                     self._b('b_out', [self.n_out]))
    # end method add_output_layer


    def add_backward_path(self):
        self.lr = tf.placeholder(tf.float32)
        self.loss = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(logits=self.logits, labels=self.Y))
        self.train_op = tf.train.AdamOptimizer(self.lr).minimize(self.loss)
        self.acc = tf.reduce_mean(tf.cast(tf.equal(tf.argmax(self.logits, 1),tf.argmax(self.Y, 1)), tf.float32))
    # end method add_backward_path


    def _W(self, name, shape):
        return tf.get_variable(name, shape, tf.float32, tf.truncated_normal_initializer(stddev=0.1))
    # end method _W

    
    def _b(self, name, shape):
        return tf.get_variable(name, shape, tf.float32, tf.constant_initializer(0.1))
    # end method _b


    def fit(self, X, Y, val_data=None, n_epoch=10, batch_size=128, keep_prob=0.5, en_exp_decay=True,
            en_shuffle=True):
        if val_data is None:
            print("Train %d samples" % len(X))
        else:
            print("Train %d samples | Test %d samples" % (len(X), len(val_data[0])))
        log = {'loss':[], 'acc':[], 'val_loss':[], 'val_acc':[]}
        global_step = 0

        self.sess.run(tf.global_variables_initializer()) # initialize all variables
        for epoch in range(n_epoch):
            if en_shuffle:
                X, Y = sklearn.utils.shuffle(X, Y)
            local_step = 1

            for X_batch, Y_batch in zip(self.gen_batch(X, batch_size),
                                        self.gen_batch(Y, batch_size)): # batch training
                lr = self.decrease_lr(en_exp_decay, global_step, n_epoch, len(X), batch_size) 
                _, loss, acc = self.sess.run([self.train_op, self.loss, self.acc],
                                              feed_dict={self.X:X_batch, self.Y:Y_batch,
                                                         self.lr:lr, self.keep_prob:keep_prob})
                local_step += 1
                global_step += 1
                if local_step % 50 == 0:
                    print ("Epoch %d/%d | Step %d/%d | train_loss: %.4f | train_acc: %.4f | lr: %.4f"
                           %(epoch+1, n_epoch, local_step, int(len(X)/batch_size), loss, acc, lr))

            if val_data is not None: # go through test dara, compute averaged validation loss and acc
                val_loss_list, val_acc_list = [], []
                for X_test_batch, Y_test_batch in zip(self.gen_batch(val_data[0], batch_size),
                                                      self.gen_batch(val_data[1], batch_size)):
                    v_loss, v_acc = self.sess.run([self.loss, self.acc],
                                                   feed_dict={self.X:X_test_batch, self.Y:Y_test_batch,
                                                              self.keep_prob:1.0})
                    val_loss_list.append(v_loss)
                    val_acc_list.append(v_acc)
                val_loss, val_acc = self.list_avg(val_loss_list), self.list_avg(val_acc_list)

            # append to log
            log['loss'].append(loss)
            log['acc'].append(acc)
            if val_data is not None:
                log['val_loss'].append(val_loss)
                log['val_acc'].append(val_acc)
            # verbose
            if val_data is None:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                    "lr: %.4f" % (lr) )
            else:
                print ("Epoch %d/%d | train_loss: %.4f | train_acc: %.4f |" % (epoch+1, n_epoch, loss, acc),
                    "test_loss: %.4f | test_acc: %.4f |" % (val_loss, val_acc),
                    "lr: %.4f" % (lr) )

        return log
    # end method fit


    def predict(self, X_test, batch_size=128):
        batch_pred_list = []
        for X_test_batch in self.gen_batch(X_test, batch_size):
            batch_pred = self.sess.run(self.logits, feed_dict={self.X:X_test_batch, self.keep_prob:1.0})
            batch_pred_list.append(batch_pred)
        return np.concatenate(batch_pred_list)
    # end method predict


    def gen_batch(self, arr, batch_size):
        for i in range(0, len(arr), batch_size):
            yield arr[i : i+batch_size]
    # end method gen_batch


    def decrease_lr(self, en_exp_decay, global_step, n_epoch, len_X, batch_size):
        if en_exp_decay:
            max_lr = 0.003
            min_lr = 0.0001
            decay_rate = math.log(min_lr/max_lr) / (-n_epoch*len_X/batch_size)
            lr = max_lr*math.exp(-decay_rate*global_step)
        else:
            lr = 0.001
        return lr
    # end method adjust_lr


    def list_avg(self, l):
        return sum(l) / len(l)
    # end method list_avg
# end class LinearSVMClassifier