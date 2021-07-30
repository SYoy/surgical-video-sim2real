
Data Generation:
----------------

Training data:
```
cd simulated_data_generation
blender ExampleScene_FakeLiver.blend -b -P renderRandomImages.py -- --images 10 --test_render --texture_patch_size 512
```

Synthetic reference images and render data (texture-pixel correspondences, interpolation weights) for domain A will be written to ```data/simulated/```.

Test data:
```
cd simulated_data_generation
blender ExampleScene_FakeLiver.blend -b -P renderSequences.py -- --test_render --texture_patch_size 512
```

This will be written to ```data/simulated_sequences/```. Note that the ```train.py``` script translates and saves test images every N iterations. So test data has to be generated before training.


Training:
----------------

After synthetic data was generated, run the following to train the model:
```
cd translation_model
python3 train.py --output_path trials/test_trial
```

Dependences:
----------------

The code was tested on:
> Python 3.8.5, Pytorch 1.5.0, Blender 2.79b, Torchvision 0.6.0