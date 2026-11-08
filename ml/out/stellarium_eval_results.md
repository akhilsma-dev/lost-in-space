100 Stellarium frames, 10.3 min wall

| method | correct % | any solve % | median att err (arcsec) | centroids/frame (median) | cat recall (mag<=5.5) | top-20 hit | s/frame |
|---|---:|---:|---:|---:|---:|---:|---:|
| tetra3 | 4 | 4 | 5 | 1119 | 0.65 | 0.98 | 4.91 |
| tetra3-tuned | 100 | 100 | 18 | 1151 | 0.98 | 0.97 | 2.04 |
| StarNet | 38 | 38 | 65 | 60 | 0.58 | 0.94 | 14.85 |
| StarNet star+planet (diag) | 88 | 88 | 46 | 76 | 0.72 | 0.93 | 15.50 |

What StarNet calls each in-frame catalogue star (fraction per magnitude bin; nearest detection within 3 px, else missed):

| mag bin | n | glare | hot_pixel | missed | planet | star |
|---|---:|---:|---:|---:|---:|---:|
| (-2.0, 1.0] | 51 | 0 | 0 | 0.86 | 0.14 | 0 |
| (1.0, 2.0] | 142 | 0 | 0 | 0.45 | 0.55 | 0 |
| (2.0, 3.0] | 450 | 0 | 0 | 0.15 | 0.79 | 0.06 |
| (3.0, 4.0] | 1161 | 0 | 0.01 | 0.06 | 0.88 | 0.06 |
| (4.0, 4.5] | 1363 | 0 | 0.03 | 0.04 | 0.13 | 0.79 |
| (4.5, 5.0] | 2614 | 0 | 0.06 | 0.09 | 0.01 | 0.84 |
| (5.0, 5.5] | 4280 | 0 | 0.32 | 0.3 | 0.01 | 0.38 |

StarNet flags glare in 100% of frames (there is no glare in these images).
