from time import time
import bpy, bmesh
from mathutils.bvhtree import BVHTree
from mathutils import Vector, Matrix
import torch
import sys
from PIL import Image
import numpy as np
import os
from PIL import Image
import utils
import cv2

# Add path to this file to the sys path to be able to import local files:
blenderFilePath = bpy.path.abspath("//")
sys.path.append(blenderFilePath)

from renderer import Renderer
from utils import *

class Raycaster:

	def __init__( self, sceneName, imW, imH, texPatchW, texPatchH , generate_sequence=False ):

		#objects = [self.addObject( bpy.data.objects[o] ) for o in ['Liver','Fat','Ligament','AbdominalWall','Gallbladder']]
		list_of_possible_objects = ["Liver","Fat","Ligament","AbdominalWall","Gallbladder"]
		objects = []
		for name in list_of_possible_objects:
			try:
				obj = self.addObject( bpy.data.objects[name] )
				objects.append(obj)
			except:
				pass

		texPatchW = texPatchW
		texPatchH = texPatchH
		texPatchSize = texPatchW
		numTexPatches = 6
		texW = len(list_of_possible_objects)*texPatchW + 1
		texH = texPatchH*numTexPatches + 1

		tex_tmp = [
			load_img_tensor(os.path.join('texture_patches/',f),texPatchSize)
			for f
			in sorted(os.listdir('texture_patches'))
			if '.png' in f and '_' in f
		]
		tex_tmp = torch.cat(tex_tmp, dim=2).repeat((1,numTexPatches,1))
		tex = torch.zeros(3,texH,texW)
		tex[:,:-1,:-1] = tex_tmp
		tex = tex.view(3,-1)

		texNormals = torch.unsqueeze(torch.cat((-torch.diag(torch.ones(3)),torch.diag(torch.ones(3)))),dim=1)
		texNums = torch.arange(numTexPatches,dtype=torch.float).view(-1,1,1)

		pIm = torch.stack(torch.meshgrid(torch.arange(imW),torch.arange(imH))).permute(1,2,0).view(-1,2)

		save_img_tensor(tex.view(3,texH,texW),"tex.png")

		self.imW = imW
		self.imH = imH
		self.texW = texW
		self.texH = texH
		self.texPatchW = texPatchW
		self.texPatchH = texPatchH
		self.tex = tex
		self.texNormals = texNormals
		self.texNums = texNums
		self.pIm = pIm
		self.objects = objects
		self.sceneName = sceneName
		if generate_sequence:
			self.outPath = os.path.join( "../data/simulated_sequences", sceneName )
		else:
			self.outPath = os.path.join( "../data/simulated", sceneName )

		if not os.path.exists( self.outPath ):
			os.makedirs( self.outPath )

	def luminosity(self,depth,a=.000035, b=.0004, c=.4):
		depth = torch.clamp(depth, max=100)
		attenuation = a*(depth**2) + b*depth + c
		luminosity = 1 / attenuation
		return luminosity

	def continuous( self, point, width ):
		pX = point.index_select(dim=-1,index=torch.LongTensor([0]))
		pY = point.index_select(dim=-1,index=torch.LongTensor([1]))
		return torch.squeeze(pX + pY*width, dim=-1)

	def boundingBox( self, bm ):
		xs = [v.co.x for v in bm.verts]
		ys = [v.co.y for v in bm.verts]
		zs = [v.co.z for v in bm.verts]
		minPos = Vector((min(xs), min(ys), min(zs)))
		maxPos = Vector((max(xs), max(ys), max(zs)))
		return {"min":minPos, "max":maxPos, "size":maxPos-minPos}

	def addObject( self, obj ):

		# Object to world coordinates (makes ray casting simpler later on):
		obj.select = True
		bpy.context.scene.objects.active = obj
		bpy.ops.object.transform_apply( location=True, rotation=True, scale=True )

		# Smooth the mesh:
		mesh = obj.data
		for f in mesh.polygons:
			f.use_smooth = True

		bm = bmesh.new()
		bm.from_mesh(obj.data)

		# triangulate meshes because we use barycentric coordinates later on:
		bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method=0, ngon_method=0)

		#bmesh.ops.transform(bm, matrix=obj.matrix_world.inverted(), verts=bm.verts)
		bb = self.boundingBox(bm)

		# Create a tree for the object of interest:
		bvhtree = BVHTree.FromBMesh(bm)

		o = { "bmesh": bm,
				"bounds": bb,
				"tree": bvhtree }


		return o

	def createRay( self, x, y , cam, frame ):
		origin = cam.location
		pixel3DPos = frame[3] + x/self.imW*(frame[0]-frame[3]) + y/self.imH*(frame[2]-frame[3])
		
		dir = (pixel3DPos - origin).normalized()
		
		return origin, dir

	# Compute barycentric coordinates (u, v, w) for
	# point p with respect to triangle (a, b, c)
	# from https://gamedev.stackexchange.com/questions/23743/whats-the-most-efficient-way-to-find-barycentric-coordinates
	def barycentric( self, p, a, b, c ):
		v0 = b - a
		v1 = c - a
		v2 = p - a
		d00 = v0.dot(v0)
		d01 = v0.dot(v1)
		d11 = v1.dot(v1)
		d20 = v2.dot(v0)
		d21 = v2.dot(v1)
		denom = d00 * d11 - d01 * d01
		v = (d11 * d20 - d01 * d21) / denom
		w = (d00 * d21 - d01 * d20) / denom
		u = 1.0 - v - w
		return u, v, w

	def sceneRayCast( self, origin, direction, objects ):

		minDist = 1e10
		objID = None
		location = None
		normal = None
		index = None

		for i in range(len(objects)):
			bm = objects[i]["bmesh"]
			tree = objects[i]["tree"]
			l, n, ind, d = tree.ray_cast(origin, direction)
			if l is not None and d < minDist:
				location = l
				index = ind
				minDist = d
				objID = i
				f = bm.faces[ind]
				vs = f.verts
				u,v,w = self.barycentric( l, vs[0].co, vs[1].co, vs[2].co )
				normal = u*vs[0].normal + v*vs[1].normal + w*vs[2].normal
				normal = normal.normalized()
				#normal = n.normalized()
		return objID, location, normal

	def hitToTextureCoords( self, location, normal, texNormal, bounds ):
		dotProduct = dot(normal,texNormal)
		if dotProduct <= 0:
			return None, None
		weight = dotProduct**2
		locPos = location - bounds["min"]
		relPos = Vector( [ (l-m)/s for l,m,s in zip(location,bounds["min"],bounds["size"]) ] )
		
		# Find the directions orthogonal to texture normal vector and use those positions as
		# texture coordinates:
		texPosRel = []
		for i in range(0,3):
			if abs(texNormal[i]) < 1e-9: # For those entries 0 (i.e. orthogonal to unit vector)
				texPosRel.append( relPos[i] )
		
		return texPosRel, weight

	# loosely based on:
	# https://blender.stackexchange.com/questions/38009/3x4-camera-matrix-from-blender-camera
	def get_projection_matrix(self,cam,first_px,last_px):

		# world to camera matrix (RT)
		location, rotation = cam.matrix_world.decompose()[0:2]
		R = rotation.to_matrix().transposed()
		T = -1*R * location
		RT = Matrix((
			R[0][:] + (T[0],),
			R[1][:] + (T[1],),
			R[2][:] + (T[2],)
		))
		R_bcam2cv = Matrix((
			(1, 0,  0),
			(0, -1, 0),
			(0, 0, -1)
		))
		RT = R_bcam2cv * RT

		### PARAMETRS ###
		# convert frame to homogenous camera coordinates
		first_px = utils.project_vec(RT*utils.homogeneous_vec(first_px))
		last_px = utils.project_vec(RT*utils.homogeneous_vec(last_px))
		
		# height/width in image/camera space + upper left corner
		x_0, y_0 = first_px[:2]
		w_c, h_c = (last_px - first_px)[:2]
		w_i, h_i = self.imW-1, self.imH-1
		# scaling parameters
		a_u, a_v = w_i/w_c, h_i/h_c
		# principal points
		u_0, v_0 = -a_u*x_0, -a_v*y_0
		
		# camera calibration matrix (C)
		C = Matrix((
			(a_u,  0,u_0),
			(  0,a_v,v_0),
			(  0,  0,  1)
		))
		return torch.Tensor(C*RT)

	def raycast( self, imageID=0 , generate_sequence=False):

		t0 = time()
		# set frame
		#print('frame ',frame_no)
		#self.scn.frame_set(frame_no)
		scn = bpy.context.scene
		cam = bpy.data.objects["Camera"]
		if generate_sequence:
			scn.frame_set(imageID)
		frame = cam.data.view_frame(scn)
		# Its in local space so transform to global
		frame = [cam.matrix_world * corner for corner in frame]
		for i in range(4):
			bpy.data.objects["C{}".format(i+1)].location = frame[i]

		bpy.data.objects["C5"].location = cam.location

		#bpy.ops.wm.save_mainfile(filepath="{}.blend".format(imageID))

		# get raycast hits from blender (slooow)
		objIDs,locs,normals,b_min,b_size,light_dir  = [],[],[],[],[],[]

		for o in self.objects:
			o["bmesh"].faces.ensure_lookup_table()

		#normalImage = np.zeros( (self.imH, self.imW, 3) )
		
		for x in range(self.imW):
			for y in range(self.imH):
				origin, direction = self.createRay(x,y,cam,frame)
				objID, location, normal = self.sceneRayCast(origin, direction, self.objects)
				b_min.append([m for m in self.objects[objID]['bounds']['min']])
				b_size.append([s for s in self.objects[objID]['bounds']['size']])
				objIDs.append([objID])
				locs.append([l for l in location])
				normals.append([n for n in normal])
				light_dir.append([d for d in direction])
				#normalImage[y,x,:] = (normal[0], normal[1], normal[2])
				#if x == 0 and y == 0:
				#    print('raycast: ',location)

		#img = Image.fromarray( ((normalImage + 1) * 128).astype(np.uint8) )
		#img.save("normals.png")

		# convert results to pytorch tensors (also slooow)
		objIDs = torch.Tensor(objIDs)
		locs = torch.Tensor(locs)
		normals = torch.Tensor(normals)
		b_min = torch.Tensor(b_min)
		b_size = torch.Tensor(b_size)
		light_src = torch.Tensor(cam.location)
		light_dir = torch.Tensor(light_dir)
		# depth for light attentuation
		depth = torch.norm(locs-light_src,dim=-1).view(self.imW,self.imH).transpose(1,0)
		# diffusion
		diffusion = torch.matmul(torch.unsqueeze(normals,dim=1),torch.unsqueeze(-light_dir,dim=2))
		diffusion = torch.clamp(diffusion,min=0,max=1).view(self.imW,self.imH).transpose(1,0)
		# 3x4 projection matrix for reprojection
		first_px, last_px = locs[0], locs[-1]
		projection_matrix = self.get_projection_matrix(cam,first_px,last_px)
		#first_px_hom, last_px_hom = torch.cat((first_px, torch.Tensor([1]))), torch.cat((last_px, torch.Tensor([1])))
		#f = torch.matmul(projection_matrix,first_px_hom)
		#l = torch.matmul(projection_matrix,last_px_hom)
		#f = f / f[-1]
		#l = l / l[-1]
		#print(f,l)
		# 3D locations of each pixel in homogeneous coordinates
		points3D = torch.unsqueeze(torch.cat((locs,torch.ones_like(locs[:,:1])),dim=-1),dim=-1)
		
		# compute texture coordinates of potential hits (triplanar mapping)
		relPos = (locs - b_min) / b_size
		texIndices = (self.texNormals==0).nonzero()[:,2].view(-1,2)
		texPosRel = torch.stack([relPos[:,i] for i in texIndices])
		pTex = torch.cat(((objIDs + texPosRel[:,:,[0]])*self.texPatchW, (self.texNums + texPosRel[:,:,[1]])*self.texPatchH),dim=-1)
		# get 4 integer corner points from float indices
		corners = torch.stack(torch.meshgrid(torch.arange(2),torch.arange(2))).permute(1,2,0).view(-1,1,2)
		pTex = torch.unsqueeze(pTex,dim=1)
		rest = pTex - pTex.floor()
		# create mask which indicates hits (1) and misses (0) for each texture view
		n_dot = torch.sum(normals*self.texNormals,dim=-1,keepdim=True)
		hits = n_dot.gt(0).type(torch.float).transpose(1,2)
		# compute weights (combined binlinear coefficients and texture patch (triplanar) weights)
		cornerWeights = torch.prod(rest+corners.type(torch.float)-1,dim=-1).abs()
		texWeights = (torch.clamp(n_dot,min=0)**2).transpose(1,2)
		weights = (texWeights*cornerWeights*hits).view(6,4,self.imW,self.imH).transpose(2,3)
		# from 2D coordinates to 1D indices
		indIm = self.continuous(self.pIm.view(1,-1,2).repeat(6,1,1),self.imW)
		indTex = self.continuous(pTex.type(torch.long) + corners,self.texW)
		# remove invalid correspondences and convert to int tensors
		indImList = [iI.index_select(dim=0,index=h.nonzero().view(-1)).type(torch.IntTensor) for [h],iI in zip(hits,indIm)]
		indTexList = [iT.index_select(dim=1,index=h.nonzero().view(-1)).type(torch.IntTensor) for [h],iT in zip(hits,indTex)]

		location, rotation = cam.matrix_world.decompose()[:2]
		cam_pose = np.array(location[:3] + rotation[:4])

		# create dictionary of correspondence data and save on disk
		corrData = {
			'indTexList': indTexList,
			'indImList': indImList,
			'weights': weights.type(torch.HalfTensor).contiguous(),
			'depth': depth.type(torch.HalfTensor),
			'diffusion': diffusion.type(torch.HalfTensor),
			'points3D': points3D,
			'projection_matrix': projection_matrix,
			'cam_pose': cam_pose
		}
		filename = os.path.join( self.outPath, 'corrData{:04d}.tar'.format(imageID))
		torch.save(corrData, filename)
		t1 = time()
		print('Time to compute and save correspondences ',t1-t0)



		
	def testRender( self, imageID ):

		t0 = time() 

		# load correspondence data
		filename = os.path.join(self.outPath, 'corrData{:04d}.tar'.format(imageID))
		corrData = torch.load( filename )
		indTexList = [t.type(torch.LongTensor).cuda() for t in corrData['indTexList']]
		indImList = [i.type(torch.LongTensor).cuda() for i in corrData['indImList']]
		weights = [w.type(torch.FloatTensor).cuda() for w in corrData['weights']]
		depth = corrData['depth'].type(torch.FloatTensor).cuda()
		diffusion = corrData['diffusion'].type(torch.FloatTensor).cuda()
		
		t1 = time()
		#print('Time to load correspondences ',t1-t0)

		# initialize renderer
		renderer = Renderer( self.imW, self.imH, self.texW, self.texH )
		# render all 6 texture views
		im = renderer.render(self.tex.cuda(),indTexList,indImList,weights)
		# add lighting
		alpha = .9
		luminosity = self.luminosity(depth)
		im = im * (diffusion * alpha + (1-alpha)) * luminosity

		#im = im.mean(dim=0,keepdim=True).repeat(3,1,1)
		# save
		filename = os.path.join( self.outPath, 'im{:04d}.png'.format(imageID))
		save_img_tensor(im.cpu(), filename)
		# save
		#filename = os.path.join( self.outPath, 'diff{:04d}.png'.format(imageID))
		#save_img_tensor(diffusion.cpu(), filename)
		# save
		#filename = os.path.join( self.outPath, 'lum{:04d}.png'.format(imageID))
		#save_img_tensor(luminosity.cpu(), filename)

		t2 = time()
		#print('Time to render image',t2-t1)