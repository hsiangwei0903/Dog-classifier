from __future__ import absolute_import, division, print_function
import torch
import models_trans.configs as configs
from models_trans.modeling import VisionTransformer, CONFIGS
from models_trans.dog_class import classes
import logging
import argparse
import os
import numpy as np
import pandas as pd
from torch.utils.data import Dataset
from torchvision import transforms
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from PIL import Image
import glob
import cv2
import copy
from scipy.optimize import linear_sum_assignment
from collections import Counter
import time

new_classes = classes
new_classes.append('cat')
new_classes.append('human')

def iou(bboxA,bboxB):
    left = max(bboxA[0],bboxB[0])
    right = min(bboxA[2],bboxB[2])
    top = max(bboxA[1],bboxB[1])
    bottom = min(bboxA[3],bboxB[3])

    if right<left or bottom < top:
        return 0.0
    iouArea = (right-left)*(bottom-top)
    bb1_Area = (bboxA[0]-bboxA[2])*(bboxA[1]-bboxA[3])
    bb2_Area = (bboxB[0]-bboxB[2])*(bboxB[1]-bboxB[3])

    return iouArea / float(bb1_Area+bb2_Area-iouArea)

def get_color(idx):
  if idx<=2:
    color_list = [(255,0,0),(0,255,0),(0,0,255)]
    return color_list[idx]
  else:
    idx = (idx+20) * 3
    color = ((37 * idx) % 255, (17 * idx) % 255, (29 * idx) % 255)

    return color

def nms_selection(results):
  boxes = []
  take = []
  for i in range(len(results.xyxy[0])):
    xmin,ymin,xmax,ymax = results.pandas().xyxy[0].iloc[i]['xmin'],results.pandas().xyxy[0].iloc[i]['ymin'],results.pandas().xyxy[0].iloc[i]['xmax'],results.pandas().xyxy[0].iloc[i]['ymax']
    boxes.append([xmin,ymin,xmax,ymax])
    take.append(True)
  for box_id in range(len(boxes)):
    for box_id2 in range(box_id+1,len(boxes)):
      if iou(boxes[box_id],boxes[box_id2])>0.8:
        take[box_id2] = False
  return take

def update_output(frame,box,id,class_id,output,output_name):
  if id not in output:
    output[id] = [[frame,box[0],box[1],box[2],box[3]]]
    output_name[id] = [class_id]
  else:
    output[id].append([frame,box[0],box[1],box[2],box[3]])
    output_name[id].append(class_id)


# load yolov5s
detector = torch.hub.load('ultralytics/yolov5', 'yolov5l')
print('yolov5 loaded')

CONFIGS = {
    'ViT-B_16': configs.get_b16_config(),
    'ViT-B_32': configs.get_b32_config(),
    'ViT-L_16': configs.get_l16_config(),
    'ViT-L_32': configs.get_l32_config(),
    'ViT-H_14': configs.get_h14_config(),
    'testing': configs.get_testing(),
}

#training model parameter setting
config = CONFIGS["ViT-B_16"]
config.slide_step = 12
config.split = 'overlap'
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

#inference data transform
test_transform = transforms.Compose([transforms.Resize((600, 600), Image.BILINEAR),transforms.CenterCrop((348, 348)),transforms.ToTensor(),transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])

#Pretrained TransFG mdoel prepared
pretrained_model_path = "/home/ubuntu/hsiangwei/TransFG/output/final_checkpoint.bin"
print("loading model from {}".format(pretrained_model_path))
model = VisionTransformer(config, 348, zero_head=True, num_classes=120, smoothing_value=0.0)
if pretrained_model_path is not None:
  pretrained_model = torch.load(pretrained_model_path)['model']
  model.load_state_dict(pretrained_model)
model.to(device)
model.eval()
print('model prepared')

seqs = [os.path.basename(x) for x in glob.glob('/home/ubuntu/hsiangwei/Fine-Grained-Object-Recognition/wyze/testing/*')]

for s_id,seq in enumerate(seqs):
  print('initiate tracking on seq {}'.format(seq))
  imgs = sorted(glob.glob('/home/ubuntu/hsiangwei/Fine-Grained-Object-Recognition/wyze/testing/{}/*'.format(seq))) # dir of images
  path_out = '/home/ubuntu/hsiangwei/Fine-Grained-Object-Recognition/wyze/final2/' # dir of output

  if not os.path.exists(path_out+seq+'/'):
    os.mkdir(path_out+seq+'/')

  tracks = []
  tracks_name = []
  tracks_age = []
  initiate = []
  
  output = {} # key is track_id, item is (frame,x1,y1,x2,y2)
  output_name = {} # key is track_id, item is list of class_id

  start = time.time()

  for frame,img in enumerate(imgs):
    if (frame+1)%30 == 0:
      print('processing frame {}'.format(frame+1))
    model.to(device)
    model.eval()
    image = Image.open(img).convert('RGB')
    results = detector(image)
    take = nms_selection(results)
    img = cv2.imread(img)
    im_out = copy.deepcopy(img)
    dets = []
    dets_name = []
    if len(results.xyxy[0])>0:
      for i in range(len(results.xyxy[0])):
        if take[i]:
          if results.pandas().xyxy[0].iloc[i]['name']=='dog': # dog detected in the image
            if results.pandas().xyxy[0].iloc[i]['confidence'] > 0.4: # setting confidence threshold
              xmin,ymin,xmax,ymax = results.pandas().xyxy[0].iloc[i]['xmin'],results.pandas().xyxy[0].iloc[i]['ymin'],results.pandas().xyxy[0].iloc[i]['xmax'],results.pandas().xyxy[0].iloc[i]['ymax'] 
              xmin,ymin,xmax,ymax = int(xmin),int(ymin),int(xmax),int(ymax)
              img_crop = image.crop((results.pandas().xyxy[0].iloc[i]['xmin'],results.pandas().xyxy[0].iloc[i]['ymin'],results.pandas().xyxy[0].iloc[i]['xmax'],results.pandas().xyxy[0].iloc[i]['ymax']))
              x = test_transform(img_crop)
              test_pred = model(x.unsqueeze(0).cuda())
              test_label = np.argmax(test_pred.cpu().data.numpy(), axis=1)
              dets.append([xmin,ymin,xmax,ymax])
              dets_name.append(test_label[-1])
          elif results.pandas().xyxy[0].iloc[i]['name']=='cat': # cat detected in the image
            if results.pandas().xyxy[0].iloc[i]['confidence'] > 0: # setting confidence threshold
              xmin,ymin,xmax,ymax = results.pandas().xyxy[0].iloc[i]['xmin'],results.pandas().xyxy[0].iloc[i]['ymin'],results.pandas().xyxy[0].iloc[i]['xmax'],results.pandas().xyxy[0].iloc[i]['ymax'] 
              xmin,ymin,xmax,ymax = int(xmin),int(ymin),int(xmax),int(ymax)
              dets.append([xmin,ymin,xmax,ymax])
              dets_name.append(120)
          elif results.pandas().xyxy[0].iloc[i]['name']=='person': # cat detected in the image
            if results.pandas().xyxy[0].iloc[i]['confidence'] > 0.4: # setting confidence threshold
              xmin,ymin,xmax,ymax = results.pandas().xyxy[0].iloc[i]['xmin'],results.pandas().xyxy[0].iloc[i]['ymin'],results.pandas().xyxy[0].iloc[i]['xmax'],results.pandas().xyxy[0].iloc[i]['ymax'] 
              xmin,ymin,xmax,ymax = int(xmin),int(ymin),int(xmax),int(ymax)
              dets.append([xmin,ymin,xmax,ymax])
              dets_name.append(121)
    
      if len(tracks)==0: # first frame
        for d_id,det in enumerate(dets): # append detection to initial tracks
          tracks.append(det)
          tracks_name.append(dets_name[d_id])
          tracks_age.append(0)
          initiate.append(True)
          update_output(frame,det,d_id,dets_name[d_id],output,output_name)

      else: # build cost matrix according to IoU of tracks and detections
        for t_id,track in enumerate(tracks):
          if tracks_age[t_id]>300:
            tracks[t_id] = [-1,-1,-1,-1] # kill track

        cost_matrix = np.ones([len(tracks),len(dets)])
        for i in range(len(tracks)):
          for j in range(len(dets)):
            if iou(tracks[i],dets[j]) > 0.05: 
              cost_matrix[i][j] = 1 - iou(tracks[i],dets[j]) # cost matrix is 1 - IoU
            else:
              cost_matrix[i][j] = 1
        
        row_ind,col_ind = linear_sum_assignment(cost_matrix) # Hungarian association between every track and detection

        for n in range(len(initiate)): # Set all the initiate to False
          initiate[n] = False

        for h,row in enumerate(row_ind): # activate initiate, update online tracks and pop detection if the association cost is lower than 0.9 (IoU > 0.1)
            if cost_matrix[row][col_ind[h]] < 0.9:
              tracks[row] = dets[col_ind[h]]
              tracks_name[row] = dets_name[col_ind[h]]
              tracks_age[row] = 0
              initiate[row] = True
              dets[col_ind[h]] = None
              dets_name[col_ind[h]] = None 
              update_output(frame,tracks[row],row,tracks_name[row],output,output_name)

        for id,unmatch_det in enumerate(dets): # initiate new tracks for those unmatch detection
          if unmatch_det:
            tracks.append(unmatch_det)
            tracks_name.append(dets_name[id])
            tracks_age.append(0)
            initiate.append(True)
            update_output(frame,unmatch_det,len(tracks),tracks_name[row],output,output_name)
            print('initiate new track')


        for t_id,track in enumerate(tracks):
          tracks_age[t_id] += 1 # add track age

  end = time.time()

  print('average FPS for seq {} is {:.2f}'.format(seq,frame/(end-start)))

  # majority vote for final classification
  for track_id in output_name:
    m_id = Counter(output_name[track_id]).most_common()[0][0]
    output_name[track_id] = m_id 
    
  final = []

  for t_id in output:
    if len(output[t_id])>5: # filter blink detection
      for label in output[t_id]:
        label.append(output_name[t_id])
        label.append(t_id)
        final.append(label)
  
  final = sorted(final,key=lambda x: x[0])  # label: frame_id,x1,y1,x2,y2,class_id,track_id

  # plotting part
  print('writing started')
  line = 0
  init = True
  while line < len(final):
    current_frame = final[line][0] # starts from 0
    if init:
      img = cv2.imread(imgs[current_frame])
      im_out = copy.deepcopy(img)
    cv2.rectangle(im_out,(int(final[line][1]),int(final[line][2])),(int(final[line][3]),int(final[line][4])),get_color(int(final[line][-1])),4)
    cv2.putText(im_out,str(final[line][-1])+'.'+str(new_classes[final[line][5]]),(int(final[line][1]),int(final[line][2])),cv2.FONT_HERSHEY_PLAIN,max(1.0, img.shape[1]/1200),(0,255,255),thickness = 2)
    line += 1
    init = False
    if line == len(final):
      break
    elif final[line][0] != current_frame:
      cv2.imwrite(path_out+seq+'/{}.jpg'.format("%04d"%current_frame),im_out)
      init = True
  
  print('writing finished')