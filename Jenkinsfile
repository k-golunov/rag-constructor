pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                echo 'Checking out code from repository'
                checkout scm
            }
        }

        stage('Backend install') {
            steps {
                sh 'python3 -m pip install --upgrade pip'
                sh 'python3 -m pip install "poetry==2.5.1"'
                sh 'poetry install'
            }
        }

        stage('Backend lint') {
            steps {
                sh 'poetry run pre-commit run --all-files --show-diff-on-failure'
            }
        }

        stage('Backend tests') {
            steps {
                sh 'poetry run pytest'
            }
        }

        stage('Frontend install') {
            steps {
                dir('frontend') {
                    sh 'npm install'
                }
            }
        }

        stage('Frontend tests') {
            steps {
                dir('frontend') {
                    sh 'npm test -- --watch=false'
                }
            }
        }

        stage('Frontend build') {
            steps {
                dir('frontend') {
                    sh 'npm run build'
                }
            }
        }
    }
}
