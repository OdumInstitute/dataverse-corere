import docker, logging, subprocess
from django.conf import settings
from corere.main import git as g
from corere.main import models as m
logger = logging.getLogger(__name__)

# def hello_list():
#     client = docker.from_env()
#     client.containers.run('r2dhttps-3a-2f-2fgithub-2ecom-2fnorvig-2fpytudesf63b403:latest', detach=True)
#     print(client.containers.list())

#TODO: Better error checking. stderr has concents even when its successful.
def build_repo2docker_image(manuscript):
    try:
        manuscript.manuscript_containerinfo.delete() #for now we just delete it each time
    except Exception as e: #TODO: make more specific
        print("EXCEPTION ", e)
        pass 

    path = g.get_submission_repo_path(manuscript)
    sub_version = manuscript.get_max_submission_version_id()
    image_name = (str(manuscript.id) + "-" + manuscript.slug + "-version" + str(sub_version))[:128] + ":" + settings.DOCKER_GEN_TAG  
    #jupyter-repo2docker  --no-run --image-name "test-version1:corere-jupyter" "../../../corere-git/10_-_manuscript_-_test10"
    run_string = "jupyter-repo2docker --no-run --json-logs --image-name '" + image_name + "' '" + path + "'"
    result = subprocess.run([run_string], shell=True, capture_output=True)
    
    container_info = m.ContainerInfo()
    container_info.image_name = image_name
    container_info.submission_version = sub_version
    container_info.manuscript = manuscript
    container_info.save()

    #print(result.stderr)

def start_repo2docker_container(manuscript):
    client = docker.from_env()
    container_info = manuscript.manuscript_containerinfo
    print(container_info.image_name)
    run_string = "jupyter notebook --ip 0.0.0.0 --NotebookApp.token='' --NotebookApp.password=''"
    container = client.containers.run(container_info.image_name, run_string, ports={'8888/tcp': 54421},detach=True)
    # while True: 
    #     print(container.logs())
    #I still need port/ip/token

    return ""#container_info.container_address()

#Run Command
# docker run -p 54321:8888 12c7e0b2e62f jupyter notebook --ip 0.0.0.0 --NotebookApp.custom_display_url=http://0.0.0.0:54321
# --NotebookApp.token=''
# --NotebookApp.password=''

#What are the steps:
# - Build Image
# - Run image as a container
#   - Eventually will need to handle the OAuth2-Proxy crap
# - Delete Container
# - Delete Image

#Thoughts:
# - How am I keeping port unique? Is there an auto option???